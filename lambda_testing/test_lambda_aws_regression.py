"""
test_lambda_aws_regression.py – Automated regression tests against a LIVE AWS
test environment.

Connects to a real AWS account using credentials resolved from the environment
or from lambda_testing/.env, invokes already-deployed Lambda functions, and
validates their responses against the same test cases in lambda_test_cases.json
used by the LocalStack suite.

Key differences from test_lambda_localstack.py
──────────────────────────────────────────────
• Uses REAL AWS credentials – no Docker / LocalStack dependency.
• Functions must already exist in the target AWS account *unless*
  LAMBDA_DEPLOY_FOR_TEST=true, in which case the handler ZIP is uploaded and
  the function is created/updated before each test case.
• S3 buckets and DynamoDB tables required by test cases are created on-demand,
  tagged with ``regression-test=true``, and optionally cleaned up at session end.
• Reports are written to  reports/aws_regression_report.{json,html}  (separate
  from the LocalStack report).

Configuration – set in lambda_testing/.env or as shell environment variables
──────────────────────────────────────────────────────────────────────────────
Paths:
  LAMBDA_TEST_CASES_FILE       Absolute or relative path to the JSON test-cases
                               file.  Relative paths are resolved from the
                               current working directory.  Allows a custom test
                               suite to be passed in from a CI pipeline.
                               Default: lambda_test_cases.json next to this file.
  LAMBDA_REPORT_DIR            Directory where JSON + HTML reports are written.
                               Relative paths are resolved from cwd.  Useful for
                               routing CI artefacts to a workspace-level folder.
                               Default: reports/ next to this file.

Connection:
  AWS_TEST_REGION              AWS region to target        (default: us-east-1)
  AWS_TEST_PROFILE             AWS CLI named profile       (optional)
  AWS_TEST_ACCESS_KEY_ID       Static access key           (optional)
  AWS_TEST_SECRET_ACCESS_KEY   Static secret key           (optional)
  AWS_TEST_SESSION_TOKEN       Temporary session token     (optional)
  AWS_TEST_ROLE_ARN            IAM role ARN to assume      (optional)
  AWS_TEST_ROLE_SESSION_NAME   AssumeRole session name     (default: lambda-regression)

Function targeting:
  LAMBDA_FUNCTION_PREFIX       Prefix prepended to every function_name from the
                               test cases (e.g. "myapp-test-" makes
                               "s3-processor" → "myapp-test-s3-processor").
  LAMBDA_TARGET_FUNCTION       If set, ALL test cases invoke this single function
                               (ignores per-case function_name / prefix).
  LAMBDA_DEPLOY_FOR_TEST       Set to "true" to upload & create/update the Lambda
                               ZIP before each test case (default: false).

Resource management:
  TEST_RESOURCE_PREFIX         Prefix for S3/DynamoDB resources created during
                               setup (default: "regtest-").
  CLEANUP_RESOURCES            "true" deletes created resources after the run
                               (default: true).

Filtering:
  REGRESSION_TEST_FILTER       Comma-separated list of test-case names or partial
                               name fragments – only matching cases are run.
                               Leave empty to run all cases.
"""

import json
import os
import time
import zipfile
from datetime import datetime, timezone
from textwrap import dedent
from typing import Optional

import boto3
import pytest
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────────────────────────
_HERE            = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT       = os.path.dirname(_HERE)
LAMBDA_CODE_FILE = os.path.join(_HERE, "sample_lambda.py")

# Load .env FIRST so that LAMBDA_TEST_CASES_FILE / LAMBDA_REPORT_DIR set
# inside .env are visible when we resolve the paths below.
load_dotenv(os.path.join(_HERE,      ".env"), override=False)
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)

# Allow the test-cases file and report directory to be specified externally
# (e.g. via CI pipeline inputs or CLI flags in run_aws_regression.sh).
# Relative paths are resolved against the current working directory so they
# work correctly both locally and inside GitHub Actions runners.
_raw_tc_file  = os.environ.get("LAMBDA_TEST_CASES_FILE", "").strip()
TEST_CASES_FILE = (
    os.path.abspath(_raw_tc_file)
    if _raw_tc_file
    else os.path.join(_HERE, "lambda_test_cases.json")
)

_raw_report_dir = os.environ.get("LAMBDA_REPORT_DIR", "").strip()
REPORT_DIR = (
    os.path.abspath(_raw_report_dir)
    if _raw_report_dir
    else os.path.join(_HERE, "reports")
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


AWS_TEST_REGION           = _cfg("AWS_TEST_REGION", "us-east-1")
AWS_TEST_PROFILE          = _cfg("AWS_TEST_PROFILE")
AWS_TEST_ACCESS_KEY_ID    = _cfg("AWS_TEST_ACCESS_KEY_ID")
AWS_TEST_SECRET_ACCESS_KEY = _cfg("AWS_TEST_SECRET_ACCESS_KEY")
AWS_TEST_SESSION_TOKEN    = _cfg("AWS_TEST_SESSION_TOKEN")
AWS_TEST_ROLE_ARN         = _cfg("AWS_TEST_ROLE_ARN")
AWS_TEST_ROLE_SESSION     = _cfg("AWS_TEST_ROLE_SESSION_NAME", "lambda-regression")

LAMBDA_FUNCTION_PREFIX    = _cfg("LAMBDA_FUNCTION_PREFIX", "")
LAMBDA_TARGET_FUNCTION    = _cfg("LAMBDA_TARGET_FUNCTION")
LAMBDA_DEPLOY_FOR_TEST    = _cfg("LAMBDA_DEPLOY_FOR_TEST", "false").lower() == "true"
LAMBDA_EXECUTION_ROLE_ARN = _cfg("LAMBDA_EXECUTION_ROLE_ARN", "")  # required when deploying

TEST_RESOURCE_PREFIX      = _cfg("TEST_RESOURCE_PREFIX", "regtest-")
CLEANUP_RESOURCES         = _cfg("CLEANUP_RESOURCES", "true").lower() != "false"
REGRESSION_TEST_FILTER    = _cfg("REGRESSION_TEST_FILTER")


# ─────────────────────────────────────────────────────────────────────────────
# Test-case loading & filtering
# ─────────────────────────────────────────────────────────────────────────────

def load_test_cases() -> list[dict]:
    with open(TEST_CASES_FILE, "r") as fh:
        cases = json.load(fh)

    if not REGRESSION_TEST_FILTER:
        return cases

    filters = [f.strip().lower() for f in REGRESSION_TEST_FILTER.split(",") if f.strip()]
    filtered = [
        tc for tc in cases
        if any(f in tc.get("name", "").lower() for f in filters)
    ]
    if not filtered:
        raise RuntimeError(
            f"REGRESSION_TEST_FILTER={REGRESSION_TEST_FILTER!r} matched no test cases. "
            f"Available names:\n" + "\n".join(f"  {tc['name']}" for tc in cases)
        )
    return filtered


def _test_case_id(tc: dict) -> str:
    return tc.get("name", "unnamed")


# ─────────────────────────────────────────────────────────────────────────────
# AWS session factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_aws_session() -> boto3.Session:
    """Build a boto3 Session for the AWS test environment.

    Credential resolution order:
      1. Static keys from AWS_TEST_ACCESS_KEY_ID / AWS_TEST_SECRET_ACCESS_KEY.
      2. Named profile from AWS_TEST_PROFILE.
      3. Default boto3 credential chain (instance profile, env vars, ~/.aws).

    If AWS_TEST_ROLE_ARN is also set, the resolved credentials are then used to
    call STS:AssumeRole and a temporary-credential session is returned.
    """
    session_kwargs: dict = {"region_name": AWS_TEST_REGION}

    if AWS_TEST_ACCESS_KEY_ID and AWS_TEST_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"]     = AWS_TEST_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = AWS_TEST_SECRET_ACCESS_KEY
        if AWS_TEST_SESSION_TOKEN:
            session_kwargs["aws_session_token"] = AWS_TEST_SESSION_TOKEN
        session = boto3.Session(**session_kwargs)
        print(f"[aws-session] Using static credentials (key id …{AWS_TEST_ACCESS_KEY_ID[-4:]})")
    elif AWS_TEST_PROFILE:
        session = boto3.Session(profile_name=AWS_TEST_PROFILE, region_name=AWS_TEST_REGION)
        print(f"[aws-session] Using AWS profile: {AWS_TEST_PROFILE!r}")
    else:
        session = boto3.Session(**session_kwargs)
        identity = session.client("sts").get_caller_identity()
        print(f"[aws-session] Using default credential chain – Account: {identity['Account']}")

    if AWS_TEST_ROLE_ARN:
        sts = session.client("sts")
        assumed = sts.assume_role(
            RoleArn=AWS_TEST_ROLE_ARN,
            RoleSessionName=AWS_TEST_ROLE_SESSION,
        )
        creds = assumed["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=AWS_TEST_REGION,
        )
        print(f"[aws-session] Assumed role: {AWS_TEST_ROLE_ARN}")

    # Verify credentials by calling GetCallerIdentity
    identity = session.client("sts").get_caller_identity()
    print(
        f"[aws-session] Authenticated → "
        f"Account={identity['Account']}  "
        f"UserId={identity['UserId']}  "
        f"Region={AWS_TEST_REGION}"
    )
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Resource helpers
# ─────────────────────────────────────────────────────────────────────────────

_REGRESSION_TAG = {"Key": "regression-test", "Value": "true"}


def _prefixed(name: str) -> str:
    """Apply TEST_RESOURCE_PREFIX to a resource name."""
    return f"{TEST_RESOURCE_PREFIX}{name}" if TEST_RESOURCE_PREFIX else name


def _setup_resources(
    clients: dict,
    setup_config: dict,
    created: list[dict],
) -> None:
    """Create S3 buckets and DynamoDB tables declared in a test case 'setup' block.

    Every resource created is appended to *created* so the session-teardown
    fixture can delete it when CLEANUP_RESOURCES=true.
    """
    s3  = clients["s3"]
    ddb = clients["dynamodb"]

    for raw_bucket in setup_config.get("s3_buckets", []):
        bucket = _prefixed(raw_bucket)
        try:
            if AWS_TEST_REGION == "us-east-1":
                s3.create_bucket(Bucket=bucket)
            else:
                s3.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": AWS_TEST_REGION},
                )
            s3.put_bucket_tagging(
                Bucket=bucket,
                Tagging={"TagSet": [_REGRESSION_TAG]},
            )
            created.append({"type": "s3_bucket", "name": bucket})
            print(f"  [setup] S3 bucket created: {bucket}")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise
            print(f"  [setup] S3 bucket already exists (reusing): {bucket}")

    for raw_table in setup_config.get("dynamodb_tables", []):
        table_def = dict(raw_table)
        table_def["TableName"] = _prefixed(table_def["TableName"])
        try:
            ddb.create_table(**table_def)
            ddb.get_waiter("table_exists").wait(
                TableName=table_def["TableName"],
                WaiterConfig={"Delay": 2, "MaxAttempts": 20},
            )
            ddb.tag_resource(
                ResourceArn=ddb.describe_table(
                    TableName=table_def["TableName"]
                )["Table"]["TableArn"],
                Tags=[_REGRESSION_TAG],
            )
            created.append({"type": "dynamodb_table", "name": table_def["TableName"]})
            print(f"  [setup] DynamoDB table created: {table_def['TableName']}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUseException":
                raise
            print(f"  [setup] DynamoDB table already exists (reusing): {table_def['TableName']}")

    for raw_item in setup_config.get("dynamodb_items", []):
        item_def = dict(raw_item)
        item_def["TableName"] = _prefixed(item_def["TableName"])
        ddb.put_item(**item_def)
        print(f"  [setup] DynamoDB item seeded in {item_def['TableName']}")


def _cleanup_resources(clients: dict, created: list[dict]) -> None:
    """Delete every resource recorded in *created*."""
    if not CLEANUP_RESOURCES:
        print(f"\n[cleanup] CLEANUP_RESOURCES=false – skipping deletion of {len(created)} resource(s).")
        return

    s3  = clients["s3"]
    ddb = clients["dynamodb"]

    for res in reversed(created):
        rtype = res["type"]
        rname = res["name"]
        try:
            if rtype == "s3_bucket":
                # Empty the bucket first
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=rname):
                    objects = page.get("Contents", [])
                    if objects:
                        s3.delete_objects(
                            Bucket=rname,
                            Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
                        )
                s3.delete_bucket(Bucket=rname)
                print(f"  [cleanup] Deleted S3 bucket: {rname}")
            elif rtype == "dynamodb_table":
                ddb.delete_table(TableName=rname)
                print(f"  [cleanup] Deleted DynamoDB table: {rname}")
        except ClientError as exc:
            print(f"  [cleanup] Warning – could not delete {rtype} {rname!r}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Lambda deployment helper (only used when LAMBDA_DEPLOY_FOR_TEST=true)
# ─────────────────────────────────────────────────────────────────────────────

def _create_lambda_zip(source_file: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_file, arcname="lambda_function.py")


def _deploy_lambda(
    lambda_client,
    function_name: str,
    handler: str,
    source_path: str,
    timeout: int,
    memory_size: int,
    env_vars: dict,
    zip_cache: dict,
) -> None:
    """Upload and create/update the Lambda function in the real AWS account."""
    if not LAMBDA_EXECUTION_ROLE_ARN:
        raise RuntimeError(
            "LAMBDA_DEPLOY_FOR_TEST=true requires LAMBDA_EXECUTION_ROLE_ARN to be set."
        )

    # Build / reuse ZIP
    norm_src = os.path.normpath(source_path)
    if norm_src not in zip_cache:
        zip_path = os.path.join(_HERE, f"_aws_deploy_{len(zip_cache)}.zip")
        _create_lambda_zip(norm_src, zip_path)
        zip_cache[norm_src] = zip_path
    with open(zip_cache[norm_src], "rb") as fh:
        zip_bytes = fh.read()

    # Attempt update; fall back to create
    try:
        lambda_client.update_function_code(
            FunctionName=function_name,
            ZipFile=zip_bytes,
        )
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Handler=handler,
            Timeout=timeout,
            MemorySize=memory_size,
            Environment={"Variables": env_vars},
        )
        # Wait for update to complete
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name, WaiterConfig={"Delay": 3, "MaxAttempts": 20})
        print(f"  [deploy] Updated existing function: {function_name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("ResourceNotFoundException",):
            raise
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime="python3.11",
            Role=LAMBDA_EXECUTION_ROLE_ARN,
            Handler=handler,
            Code={"ZipFile": zip_bytes},
            Timeout=timeout,
            MemorySize=memory_size,
            Environment={"Variables": env_vars},
            Tags={"regression-test": "true"},
        )
        waiter = lambda_client.get_waiter("function_active")
        waiter.wait(FunctionName=function_name, WaiterConfig={"Delay": 3, "MaxAttempts": 30})
        print(f"  [deploy] Created new function: {function_name}")


# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_response(
    validations: dict,
    response_payload: dict,
    function_error: Optional[str],
    raw_payload: bytes,
    clients: dict,
    failures: list[str],
    env_prefix: str = "",
) -> None:
    """Apply all declared validations and append failure messages to *failures*."""

    def _fail(msg: str) -> None:
        failures.append(msg)
        print(f"  [FAIL] {msg}")

    # ── FunctionError ──────────────────────────────────────────────────────
    expected_fe = validations.get("expected_function_error")
    if expected_fe is not None:
        if function_error is None:
            _fail(
                f"Expected FunctionError={expected_fe!r} but Lambda returned no error."
            )
    else:
        if function_error:
            err_msg = response_payload.get("errorMessage", raw_payload.decode(errors="replace"))
            _fail(f"Lambda raised FunctionError={function_error!r}: {err_msg}")

    # ── HTTP-style response ────────────────────────────────────────────────
    if "lambda_response" in validations:
        expected = validations["lambda_response"]

        if "statusCode" in expected:
            actual_sc = response_payload.get("statusCode")
            if actual_sc != expected["statusCode"]:
                _fail(
                    f"statusCode mismatch: expected {expected['statusCode']}, got {actual_sc}"
                )

        if "body" in expected:
            actual_body = response_payload.get("body")
            exp_body    = expected["body"]
            if actual_body != exp_body:
                _fail(
                    f"body mismatch:\n"
                    f"    expected: {exp_body!r}\n"
                    f"    actual  : {actual_body!r}"
                )

        if "body_contains" in expected:
            actual_body = response_payload.get("body", "")
            fragment    = expected["body_contains"]
            if fragment not in str(actual_body):
                _fail(
                    f"body_contains not found: {fragment!r} not in {actual_body!r}"
                )

    # ── Raw JSON response (Chime SMA style) ───────────────────────────────
    if "response_json" in validations:
        for rj_key, rj_expected in validations["response_json"].items():
            rj_actual = response_payload.get(rj_key)
            if rj_actual != rj_expected:
                _fail(
                    f"response_json['{rj_key}']: expected {rj_expected!r}, "
                    f"got {rj_actual!r}"
                )

    # ── First action type ─────────────────────────────────────────────────
    if "response_first_action_type" in validations:
        expected_type = validations["response_first_action_type"]
        actions_list  = response_payload.get("Actions", [])
        if not actions_list:
            _fail(
                f"response_first_action_type={expected_type!r}: "
                f"Actions array is empty or missing."
            )
        else:
            actual_type = actions_list[0].get("Type")
            if actual_type != expected_type:
                _fail(
                    f"response_first_action_type: expected {expected_type!r}, "
                    f"got {actual_type!r}"
                )

    # ── DynamoDB item ──────────────────────────────────────────────────────
    if "dynamodb_item" in validations:
        ddb_val  = validations["dynamodb_item"]
        table    = env_prefix + ddb_val["TableName"]
        key      = ddb_val["Key"]
        expected_attrs = ddb_val.get("ExpectedAttributeValue", {})
        try:
            item_resp = clients["dynamodb"].get_item(TableName=table, Key=key)
            item = item_resp.get("Item")
            if item is None:
                _fail(f"DynamoDB: item not found in table={table!r} key={key}")
            else:
                for attr_name, attr_val in expected_attrs.items():
                    actual_val = item.get(attr_name)
                    if actual_val != attr_val:
                        _fail(
                            f"DynamoDB attr mismatch [{attr_name}]: "
                            f"expected={attr_val!r}, actual={actual_val!r}"
                        )
        except ClientError as exc:
            _fail(f"DynamoDB get_item error (table={table!r}): {exc}")

    # ── S3 object ─────────────────────────────────────────────────────────
    if "s3_object" in validations:
        s3_val  = validations["s3_object"]
        bucket  = env_prefix + s3_val["Bucket"]
        key     = s3_val["Key"]
        expected_content  = s3_val.get("ExpectedContent")
        expected_contains = s3_val.get("ContentContains")
        try:
            obj  = clients["s3"].get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read().decode("utf-8")
            print(f"  [s3] s3://{bucket}/{key} content: {body!r}")
            if expected_content is not None and body != expected_content:
                _fail(
                    f"S3 content mismatch for s3://{bucket}/{key}:\n"
                    f"    expected: {expected_content!r}\n"
                    f"    actual  : {body!r}"
                )
            if expected_contains and expected_contains not in body:
                _fail(
                    f"S3 content_contains not found: {expected_contains!r} "
                    f"not in s3://{bucket}/{key}"
                )
        except ClientError as exc:
            _fail(f"S3 get_object error for s3://{bucket}/{key}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Report generation
# ─────────────────────────────────────────────────────────────────────────────

def _write_html_report(
    results: list[dict],
    summary: dict,
    output_path: str,
    env_info: dict,
) -> None:
    """Generate a self-contained HTML regression report."""

    def _badge(outcome: str) -> str:
        colours = {
            "PASSED":  ("#27ae60", "PASS"),
            "FAILED":  ("#e74c3c", "FAIL"),
            "SKIPPED": ("#f39c12", "SKIP"),
            "ERROR":   ("#8e44ad", "ERRO"),
        }
        bg, label = colours.get(outcome, ("#7f8c8d", outcome[:4]))
        return (
            f'<span style="background:{bg};color:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:0.82em;font-weight:700">{label}</span>'
        )

    rows = []
    for r in results:
        error_html = ""
        if r.get("error"):
            # Escape and wrap in pre block
            escaped = (
                r["error"]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            error_html = (
                f'<pre style="background:#fef9f0;border:1px solid #fad7a0;'
                f'padding:8px;border-radius:4px;margin-top:6px;'
                f'font-size:0.82em;white-space:pre-wrap;word-break:break-word">'
                f'{escaped}</pre>'
            )
        rows.append(
            f"<tr>"
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;'
            f'max-width:420px;word-break:break-word">'
            f'{r["name"]}{error_html}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">'
            f'{_badge(r["outcome"])}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">'
            f'{r["duration_s"]:.3f}s</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;'
            f'font-size:0.8em;color:#666">{r["timestamp"]}</td>'
            f"</tr>"
        )

    env_rows = "".join(
        f"<tr><td style='padding:4px 10px;font-weight:600;color:#555'>{k}</td>"
        f"<td style='padding:4px 10px;font-family:monospace'>{v}</td></tr>"
        for k, v in env_info.items()
    )

    pass_pct = (
        round(summary["passed"] / summary["total"] * 100, 1) if summary["total"] else 0
    )
    fail_pct = (
        round(summary["failed"] / summary["total"] * 100, 1) if summary["total"] else 0
    )

    html = dedent(f"""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <title>AWS Lambda Regression Report</title>
      <style>
        body {{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
               margin:0;padding:20px;background:#f5f7fa;color:#333}}
        h1   {{color:#2c3e50;margin-bottom:4px}}
        .sub {{color:#7f8c8d;font-size:0.9em;margin-bottom:24px}}
        .cards {{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
        .card {{background:#fff;border-radius:8px;padding:16px 24px;
                box-shadow:0 1px 4px rgba(0,0,0,.1);min-width:130px;text-align:center}}
        .card .num {{font-size:2em;font-weight:700}}
        .card .lbl {{font-size:0.8em;color:#888;margin-top:2px}}
        .c-pass {{color:#27ae60}} .c-fail {{color:#e74c3c}}
        .c-skip {{color:#f39c12}} .c-err  {{color:#8e44ad}} .c-tot {{color:#2980b9}}
        table  {{border-collapse:collapse;width:100%;background:#fff;
                 border-radius:8px;overflow:hidden;
                 box-shadow:0 1px 4px rgba(0,0,0,.1)}}
        thead  {{background:#2c3e50;color:#fff}}
        thead th {{padding:10px 12px;text-align:left;font-size:0.88em;font-weight:600}}
        tbody tr:hover {{background:#f0f4f8}}
        .env-table {{width:auto;margin-bottom:24px;font-size:0.88em}}
        .env-table td {{border:1px solid #ddd;background:#fff}}
        .bar-bg {{background:#e0e0e0;border-radius:4px;height:10px;margin-top:8px;overflow:hidden}}
        .bar-pass {{height:100%;background:#27ae60;width:{pass_pct}%}}
        .coverage {{font-size:0.78em;color:#555;margin-top:4px}}
      </style>
    </head>
    <body>
      <h1>&#x1F9EA; AWS Lambda Regression Report</h1>
      <p class="sub">
        Generated: {summary.get("generated_at","–")} &nbsp;|&nbsp;
        Region: {env_info.get("Region","–")} &nbsp;|&nbsp;
        Account: {env_info.get("Account","–")}
      </p>

      <div class="cards">
        <div class="card"><div class="num c-tot">{summary["total"]}</div>
          <div class="lbl">Total</div></div>
        <div class="card"><div class="num c-pass">{summary["passed"]}</div>
          <div class="lbl">Passed</div></div>
        <div class="card"><div class="num c-fail">{summary["failed"]}</div>
          <div class="lbl">Failed</div></div>
        <div class="card"><div class="num c-skip">{summary["skipped"]}</div>
          <div class="lbl">Skipped</div></div>
        <div class="card"><div class="num c-err">{summary["errored"]}</div>
          <div class="lbl">Errors</div></div>
        <div class="card">
          <div class="num c-pass">{pass_pct}%</div>
          <div class="lbl">Pass Rate</div>
          <div class="bar-bg"><div class="bar-pass"></div></div>
        </div>
      </div>

      <h2>Test Environment</h2>
      <table class="env-table">
        <tbody>{env_rows}</tbody>
      </table>

      <h2>Test Results</h2>
      <table>
        <thead>
          <tr>
            <th>Test Case</th>
            <th style="text-align:center">Result</th>
            <th style="text-align:right">Duration</th>
            <th>Timestamp</th>
          </tr>
        </thead>
        <tbody>
          {"".join(rows)}
        </tbody>
      </table>

      <h2 style="margin-top:24px">Coverage Analysis</h2>
      <table>
        <thead>
          <tr><th>Metric</th><th>Value</th><th>Coverage</th></tr>
        </thead>
        <tbody>
          {_coverage_rows(results)}
        </tbody>
      </table>

      <p style="color:#aaa;font-size:0.78em;margin-top:20px">
        Generated by test_lambda_aws_regression.py &bull; Amazon Connect Testing Suite
      </p>
    </body>
    </html>
    """)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)


def _coverage_rows(results: list[dict]) -> str:
    """Generate HTML table rows for the coverage section."""
    total   = len(results)
    passed  = sum(1 for r in results if r["outcome"] == "PASSED")
    failed  = sum(1 for r in results if r["outcome"] == "FAILED")
    skipped = sum(1 for r in results if r["outcome"] == "SKIPPED")
    errored = sum(1 for r in results if r["outcome"] == "ERROR")
    executed = total - skipped

    def _bar(pct: float, colour: str) -> str:
        return (
            f'<div style="background:#e0e0e0;border-radius:4px;height:8px;width:160px;overflow:hidden">'
            f'<div style="height:100%;width:{pct:.0f}%;background:{colour}"></div></div>'
        )

    rows = [
        ("Total test cases",   total,    100,    "#2980b9"),
        ("Executed (non-skip)", executed, (executed/total*100 if total else 0), "#16a085"),
        ("Passed",             passed,   (passed/total*100  if total else 0), "#27ae60"),
        ("Failed",             failed,   (failed/total*100  if total else 0), "#e74c3c"),
        ("Skipped",            skipped,  (skipped/total*100 if total else 0), "#f39c12"),
        ("Errors",             errored,  (errored/total*100 if total else 0), "#8e44ad"),
    ]
    html = ""
    for label, count, pct, colour in rows:
        html += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>{label}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;font-weight:700'>{count}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee'>"
            f"{_bar(pct, colour)} "
            f"<span style='font-size:0.82em;color:#666'>{pct:.1f}%</span>"
            f"</td>"
            f"</tr>"
        )
    return html


# ─────────────────────────────────────────────────────────────────────────────
# Session-level state for custom HTML report
# ─────────────────────────────────────────────────────────────────────────────

# We augment the conftest.py JSON report with our own HTML output.
# This is stored as a module-level list so the pytest_sessionfinish hook
# defined below can write the HTML once all tests have completed.
_aws_results: list[dict] = []
_aws_env_info: dict      = {}


def _record_result(name: str, outcome: str, duration: float, error: Optional[str] = None) -> None:
    _aws_results.append(
        {
            "name": name,
            "outcome": outcome,
            "duration_s": round(duration, 4),
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# pytest hooks
# ─────────────────────────────────────────────────────────────────────────────

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Intercept test results for the AWS regression HTML report."""
    outcome = yield
    report  = outcome.get_result()

    # Only record results from THIS module's test functions
    if not item.module.__name__.endswith("test_lambda_aws_regression"):
        return

    if report.when == "call" or (report.when == "setup" and report.failed):
        error_text = str(report.longrepr) if report.longrepr else None
        if report.failed:
            status = "FAILED"
        elif report.skipped:
            status = "SKIPPED"
        else:
            status  = "PASSED"
            error_text = None
        _record_result(
            name=item.name,
            outcome=status,
            duration=report.duration,
            error=error_text,
        )


def pytest_sessionfinish(session, exitstatus):
    """Write the AWS-specific HTML report at session end."""
    if not _aws_results:
        return

    passed  = sum(1 for r in _aws_results if r["outcome"] == "PASSED")
    failed  = sum(1 for r in _aws_results if r["outcome"] == "FAILED")
    skipped = sum(1 for r in _aws_results if r["outcome"] == "SKIPPED")
    errored = sum(1 for r in _aws_results if r["outcome"] == "ERROR")
    total   = len(_aws_results)

    now = datetime.now(timezone.utc).isoformat()

    summary = {
        "generated_at": now,
        "total":   total,
        "passed":  passed,
        "failed":  failed,
        "skipped": skipped,
        "errored": errored,
    }

    os.makedirs(REPORT_DIR, exist_ok=True)

    # JSON report
    json_data = {
        "report_type":  "aws_regression",
        "generated_at": now,
        "environment":  _aws_env_info,
        "summary":      summary,
        "test_cases":   _aws_results,
    }
    json_path = os.path.join(REPORT_DIR, "aws_regression_report.json")
    with open(json_path, "w") as fh:
        json.dump(json_data, fh, indent=2)

    # HTML report
    html_path = os.path.join(REPORT_DIR, "aws_regression_report.html")
    _write_html_report(_aws_results, summary, html_path, _aws_env_info)

    # Console summary
    width = 78
    print("\n" + "=" * width)
    print("  AWS LAMBDA REGRESSION TEST REPORT")
    print("=" * width)
    print(
        f"  Region   : {_aws_env_info.get('Region', '?')}   "
        f"Account : {_aws_env_info.get('Account', '?')}"
    )
    print("-" * width)
    print(f"  {'Test Case':<54} {'Result':<10} {'Duration'}")
    print("-" * width)
    for r in _aws_results:
        icon = {"PASSED": "PASS", "FAILED": "FAIL", "SKIPPED": "SKIP", "ERROR": "ERRO"}.get(
            r["outcome"], "????"
        )
        name_disp = r["name"][:52] + "..." if len(r["name"]) > 52 else r["name"]
        print(f"  [{icon}]  {name_disp:<52} {r['duration_s']:>7.3f}s")
    print("-" * width)
    pass_pct = round(passed / total * 100, 1) if total else 0
    print(
        f"  Total: {total}  Passed: {passed}  Failed: {failed}  "
        f"Skipped: {skipped}  Errors: {errored}  Pass-rate: {pass_pct}%"
    )
    print("=" * width)
    print(f"  JSON : {json_path}")
    print(f"  HTML : {html_path}")
    print("=" * width + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# pytest fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def aws_session():
    """Session-scoped boto3 Session authenticated against the AWS test environment."""
    session = _build_aws_session()
    return session


@pytest.fixture(scope="session")
def aws_regression_clients(aws_session):
    """Session-scoped dict of real AWS boto3 clients."""
    region = AWS_TEST_REGION
    clients = {
        "lambda":   aws_session.client("lambda",   region_name=region),
        "s3":       aws_session.client("s3",        region_name=region),
        "dynamodb": aws_session.client("dynamodb",  region_name=region),
        "sts":      aws_session.client("sts",        region_name=region),
    }

    # Populate the global env-info dict used by the report
    identity = clients["sts"].get_caller_identity()
    _aws_env_info.update(
        {
            "Account":               identity["Account"],
            "UserId":                identity["UserId"],
            "Region":                region,
            "Profile":               AWS_TEST_PROFILE or "(credential chain)",
            "Function prefix":       LAMBDA_FUNCTION_PREFIX or "(none)",
            "Target function":       LAMBDA_TARGET_FUNCTION or "(per test case)",
            "Deploy for test":       str(LAMBDA_DEPLOY_FOR_TEST),
            "Resource prefix":       TEST_RESOURCE_PREFIX,
            "Cleanup resources":     str(CLEANUP_RESOURCES),
        }
    )
    return clients


@pytest.fixture(scope="session")
def created_aws_resources(aws_regression_clients):
    """Collect resources created during the session; delete them at teardown."""
    created: list[dict] = []
    yield created
    _cleanup_resources(aws_regression_clients, created)


@pytest.fixture(scope="session")
def _zip_cache():
    """Simple dict used as a ZIP build cache across tests."""
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Parametrised regression test
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "test_case",
    load_test_cases(),
    ids=_test_case_id,
)
def test_lambda_aws_regression(
    aws_regression_clients,
    created_aws_resources,
    _zip_cache,
    test_case,
):
    """Run a single Lambda test case against the live AWS test environment.

    Flow
    ────
    1. Provision required S3 / DynamoDB resources in AWS (if declared in setup).
    2. Optionally deploy / update the Lambda function (LAMBDA_DEPLOY_FOR_TEST=true).
    3. Invoke the target Lambda.
    4. Validate response and side-effects.
    5. Fail the pytest test with a structured message if any assertion fails.
    """
    t_start = time.monotonic()

    name          = test_case["name"]
    handler       = test_case.get("handler", "lambda_function.lambda_handler")
    description   = test_case.get("description", "")
    timeout       = test_case.get("timeout", 30)
    memory_size   = test_case.get("memory_size", 128)
    env_vars      = test_case.get("environment_variables", {})

    # Determine which function to invoke
    if LAMBDA_TARGET_FUNCTION:
        function_name = LAMBDA_TARGET_FUNCTION
    else:
        raw_fn = test_case.get("function_name", "")
        function_name = f"{LAMBDA_FUNCTION_PREFIX}{raw_fn}" if LAMBDA_FUNCTION_PREFIX else raw_fn

    print(f"\n{'='*70}")
    print(f"  TEST    : {name}")
    print(f"  FUNCTION: {function_name}")
    if description:
        print(f"  DESC    : {description}")
    print(f"{'='*70}")

    clients = aws_regression_clients

    # ── 1. Provision resources ─────────────────────────────────────────────
    _setup_resources(
        clients,
        test_case.get("setup", {}),
        created_aws_resources,
    )

    # ── 2. Optionally deploy Lambda ────────────────────────────────────────
    if LAMBDA_DEPLOY_FOR_TEST:
        source_rel  = test_case.get("source_file")
        source_path = (
            os.path.normpath(os.path.join(_HERE, source_rel))
            if source_rel else LAMBDA_CODE_FILE
        )
        _deploy_lambda(
            clients["lambda"],
            function_name,
            handler,
            source_path,
            timeout,
            memory_size,
            env_vars,
            _zip_cache,
        )

    # ── 3. Invoke Lambda ───────────────────────────────────────────────────
    payload_str = json.dumps(test_case["trigger_event"])
    try:
        response = clients["lambda"].invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=payload_str,
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            pytest.fail(
                f"Lambda function '{function_name}' not found in AWS "
                f"(region={AWS_TEST_REGION}). "
                f"Deploy the function first, or set LAMBDA_DEPLOY_FOR_TEST=true / "
                f"LAMBDA_TARGET_FUNCTION / LAMBDA_FUNCTION_PREFIX as appropriate."
            )
        raise

    raw_payload      = response["Payload"].read()
    response_payload = json.loads(raw_payload)
    function_error   = response.get("FunctionError")

    print(f"  [invoke] FunctionError = {function_error!r}")
    print(f"  [invoke] Response:\n{json.dumps(response_payload, indent=4)}")

    # ── 4. Validate ────────────────────────────────────────────────────────
    failures: list[str] = []
    _validate_response(
        validations=test_case.get("validations", {}),
        response_payload=response_payload,
        function_error=function_error,
        raw_payload=raw_payload,
        clients=clients,
        failures=failures,
        env_prefix=TEST_RESOURCE_PREFIX,
    )

    # ── 5. Report ──────────────────────────────────────────────────────────
    duration = time.monotonic() - t_start

    if failures:
        failure_detail = (
            f"Test case '{name}' failed with {len(failures)} assertion(s):\n"
            + "\n".join(f"  • {f}" for f in failures)
        )
        print(f"\n  [FAIL] {failure_detail}")
        pytest.fail(failure_detail)

    print(f"  [PASS] {name}  ({duration:.3f}s)")
