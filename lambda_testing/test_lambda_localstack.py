"""
test_lambda_localstack.py – Lambda regression tests using LocalStack.

Each test case in lambda_test_cases.json is run as a parametrised pytest
test. The suite:
  - Spins up a LocalStack container once per session (Lambda, S3, DynamoDB).
  - Builds a deployment ZIP from sample_lambda.py.
  - Deploys and invokes each Lambda function in isolation.
  - Validates: HTTP status code, response body, DynamoDB items, S3 objects,
    and FunctionError payloads (error-path test cases).
  - Emits a JSON + HTML report via conftest.py / pytest-html.

Path resolution is based on __file__ so tests run correctly from any cwd.
"""

import atexit
import json
import os
import signal
import subprocess
import time
import zipfile

import boto3
import pytest
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from testcontainers.localstack import LocalStackContainer

# ---------------------------------------------------------------------------
# Path resolution – always relative to this file, regardless of cwd
# ---------------------------------------------------------------------------
_HERE            = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT       = os.path.dirname(_HERE)
TEST_CASES_FILE  = os.path.join(_HERE, "lambda_test_cases.json")
LAMBDA_CODE_FILE = os.path.join(_HERE, "sample_lambda.py")
LAMBDA_ZIP_FILE  = os.path.join(_HERE, "lambda_function.zip")

# Load .env from this folder first (lambda_testing/.env), then fall back to
# the repo-root .env so that suite-local overrides take precedence.
load_dotenv(os.path.join(_HERE,      ".env"), override=False)
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)

# LocalStack mock credentials (never real AWS)
_LS_CREDS = {
    "aws_access_key_id":     "test",
    "aws_secret_access_key": "test",
    "region_name":           "us-east-1",
}

# Mock IAM role ARN (LocalStack does not enforce IAM)
_MOCK_ROLE = "arn:aws:iam::000000000000:role/lambda-execution-role"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_test_cases() -> list[dict]:
    """Load all test cases from lambda_test_cases.json."""
    with open(TEST_CASES_FILE, "r") as fh:
        cases = json.load(fh)
    return cases


def _test_case_id(tc: dict) -> str:
    return tc.get("name", "unnamed")


def create_lambda_zip(source_file: str, zip_path: str) -> None:
    """Package source_file as lambda_function.py inside zip_path."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_file, arcname="lambda_function.py")


def _wait_for_function_active(lambda_client, function_name: str, timeout: int = 30) -> None:
    """Poll until the function state is Active (LocalStack is usually instant)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = lambda_client.get_function_configuration(FunctionName=function_name)
            state = resp.get("State", "")
            if state == "Active":
                return
            if state == "Failed":
                reason = resp.get("StateReason", "no reason returned")
                reason_code = resp.get("StateReasonCode", "")
                raise RuntimeError(
                    f"Function {function_name} entered Failed state. "
                    f"StateReasonCode={reason_code!r}  StateReason={reason!r}"
                )
            # Pending / Inactive – keep polling
        except ClientError:
            pass
        time.sleep(0.5)
    # LocalStack may not report State at all; treat timeout as "probably ok"
    print(f"WARNING: could not confirm Active state for {function_name} within {timeout}s – continuing")


def _mk_client(service: str, endpoint_url: str):
    return boto3.client(service, endpoint_url=endpoint_url, **_LS_CREDS)


def _cleanup_lambda_runtime_containers() -> None:
    """Remove sibling Lambda runtime containers spawned by LocalStack on the host daemon.

    LocalStack 3.x (new Lambda provider) spins up Docker containers *directly on
    the host* for each Lambda invocation.  These are NOT children of the LocalStack
    container, so they survive after LocalStack itself stops.  We enumerate them by
    name prefix and force-remove them here.
    """
    try:
        # Collect IDs from both naming schemes used across LocalStack versions
        ids: list[str] = []
        for name_filter in ("localstack_lambda", "localstack-lambda"):
            result = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"name={name_filter}"],
                capture_output=True, text=True, timeout=10,
            )
            ids.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
        # Deduplicate (Docker may return duplicates across filters)
        ids = list(dict.fromkeys(ids))
        if ids:
            subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True, timeout=15)
            print(f"[teardown] Removed {len(ids)} Lambda runtime container(s): {ids}")
        else:
            print("[teardown] No Lambda runtime containers found to remove.")
    except FileNotFoundError:
        print("[teardown] docker CLI not found – skipping Lambda runtime container cleanup.")
    except Exception as exc:
        print(f"[teardown] Warning: could not clean up Lambda runtime containers: {exc}")


def _setup_resources(clients: dict, setup_config: dict) -> None:
    """Create S3 buckets and DynamoDB tables declared in a test case's 'setup' block."""
    for bucket in setup_config.get("s3_buckets", []):
        try:
            clients["s3"].create_bucket(Bucket=bucket)
            print(f"  [setup] S3 bucket created: {bucket}")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise

    for table_def in setup_config.get("dynamodb_tables", []):
        try:
            clients["dynamodb"].create_table(**table_def)
            print(f"  [setup] DynamoDB table created: {table_def['TableName']}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUseException":
                raise

    for item_def in setup_config.get("dynamodb_items", []):
        try:
            clients["dynamodb"].put_item(
                TableName=item_def["TableName"],
                Item=item_def["Item"],
            )
            print(f"  [setup] DynamoDB item seeded: table={item_def['TableName']}")
        except ClientError as exc:
            raise


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def localstack_container():
    """Start a LocalStack container once for the entire test session.

    LocalStack 3.x uses Docker to run Lambda functions (the 'new Lambda
    provider').  The LAMBDA_EXECUTOR=local env var was removed in LocalStack
    2.0 and has no effect.  The only way to make Lambda work when LocalStack
    itself runs inside a container (as testcontainers launches it) is to
    bind-mount the host Docker socket into the LocalStack container so it can
    reach the host daemon and spin up Lambda runtime containers from inside.

    On macOS with Docker Desktop the socket is always at /var/run/docker.sock
    (Docker Desktop creates a compatibility symlink).
    On Linux CI it is also /var/run/docker.sock by default.

    Teardown guarantees
    -------------------
    * ``try/finally`` – covers normal completion and Python exceptions (incl. Ctrl+C).
    * ``atexit`` handler – covers ``sys.exit()`` calls and normal interpreter shutdown.
    * SIGTERM handler – covers CI/orchestrator kills (Docker sends SIGTERM before SIGKILL).
    * After the LocalStack container stops, :func:`_cleanup_lambda_runtime_containers`
      removes any Lambda runtime containers that LocalStack spawned on the host daemon
      (they are sibling containers and survive LocalStack's own stop).
    """
    docker_sock = "/var/run/docker.sock"

    container = LocalStackContainer(
        image="localstack/localstack:3.4.0",
    )
    container.with_services("lambda", "s3", "dynamodb")
    # Mount host Docker socket so LocalStack can launch Lambda runtime containers
    if os.path.exists(docker_sock):
        container.with_volume_mapping(docker_sock, docker_sock, "rw")
    # Prevent arm64/amd64 architecture mismatch errors on Apple Silicon Macs
    container.with_env("LAMBDA_IGNORE_ARCHITECTURE", "1")
    # Give Lambda a generous startup timeout (image pull on first run)
    container.with_env("LAMBDA_RUNTIME_ENVIRONMENT_TIMEOUT", "60")

    container.start()
    # Wait for LocalStack to be fully ready
    time.sleep(3)

    # -----------------------------------------------------------------------
    # Teardown helper – idempotent (safe to call multiple times)
    # -----------------------------------------------------------------------
    _stopped = [False]  # mutable sentinel prevents double-stop

    def _stop_everything() -> None:
        if _stopped[0]:
            return
        _stopped[0] = True
        print("\n[teardown] Stopping LocalStack container ...")
        try:
            container.stop()
            print("[teardown] LocalStack container stopped and removed.")
        except Exception as exc:
            print(f"[teardown] Warning: error stopping LocalStack container: {exc}")
        # Remove Lambda runtime containers that survived LocalStack's shutdown
        _cleanup_lambda_runtime_containers()

    # Register atexit so shutdown happens even when sys.exit() is called directly
    atexit.register(_stop_everything)

    # Intercept SIGTERM (sent by Docker / CI orchestrators before SIGKILL)
    _orig_sigterm = signal.getsignal(signal.SIGTERM)
    def _handle_sigterm(signum, frame):  # noqa: ANN001
        _stop_everything()
        if callable(_orig_sigterm):
            _orig_sigterm(signum, frame)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        yield container
    finally:
        # Primary teardown path: covers normal exit, KeyboardInterrupt, and any
        # other Python exception that unwinds the test session.
        _stop_everything()


@pytest.fixture(scope="session")
def aws_clients(localstack_container):
    """Session-scoped boto3 clients wired to the LocalStack endpoint."""
    url = localstack_container.get_url()
    return {
        "lambda":   _mk_client("lambda",   url),
        "s3":       _mk_client("s3",       url),
        "dynamodb": _mk_client("dynamodb", url),
    }


@pytest.fixture(scope="session")
def lambda_zip_factory(tmp_path_factory):
    """Session-scoped factory: build and cache a deployment ZIP per source file.

    Returns a callable ``get_zip(source_file) -> str`` that builds the ZIP on
    first call for a given source path and returns the cached path thereafter.
    This allows test cases to set a ``source_file`` field (relative to
    lambda_testing/) to package a handler from another folder, e.g. the Chime
    SMA handler in voice_testing/.

    Usage::

        zip_path = lambda_zip_factory()                      # → sample_lambda.py
        zip_path = lambda_zip_factory("/abs/path/to/foo.py") # → custom handler
    """
    _cache: dict = {}
    _base = tmp_path_factory.mktemp("lambda_pkg")

    def _get_zip(source_file: str = LAMBDA_CODE_FILE) -> str:
        path = os.path.normpath(source_file)
        if path not in _cache:
            zip_path = str(_base / f"lambda_{len(_cache)}.zip")
            create_lambda_zip(path, zip_path)
            _cache[path] = zip_path
            print(f"  [zip] Built ZIP for {os.path.basename(path)}")
        return _cache[path]

    return _get_zip


# ---------------------------------------------------------------------------
# Core test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "test_case",
    load_test_cases(),
    ids=_test_case_id,
)
def test_lambda_function(aws_clients, lambda_zip_factory, test_case):
    name          = test_case["name"]
    function_name = test_case["function_name"]
    handler       = test_case["handler"]
    description   = test_case.get("description", "")

    print(f"\n{'='*60}")
    print(f"  TEST: {name}")
    if description:
        print(f"  DESC: {description}")
    print(f"{'='*60}")

    # ------------------------------------------------------------------
    # 1. Provision any required AWS resources
    # ------------------------------------------------------------------
    _setup_resources(aws_clients, test_case.get("setup", {}))

    # ------------------------------------------------------------------
    # 2. Deploy Lambda (delete first to guarantee a fresh state)
    # ------------------------------------------------------------------
    # Resolve source file: test cases may set a custom ``source_file`` path
    # (relative to lambda_testing/) to package a handler from another folder,
    # e.g. ``"../voice_testing/chime_handler_lambda.py"``.
    source_rel  = test_case.get("source_file")
    source_path = (
        os.path.normpath(os.path.join(_HERE, source_rel))
        if source_rel else LAMBDA_CODE_FILE
    )
    with open(lambda_zip_factory(source_path), "rb") as fh:
        zip_bytes = fh.read()

    try:
        aws_clients["lambda"].delete_function(FunctionName=function_name)
        print(f"  [deploy] deleted existing function: {function_name}")
        time.sleep(0.5)
    except ClientError:
        pass  # function did not exist – that's fine

    aws_clients["lambda"].create_function(
        FunctionName=function_name,
        Runtime="python3.11",   # python3.11 is bundled in localstack/localstack:3.x
        Role=_MOCK_ROLE,
        Handler=handler,
        Code={"ZipFile": zip_bytes},
        Timeout=test_case.get("timeout", 10),
        MemorySize=test_case.get("memory_size", 128),
        Environment={"Variables": test_case.get("environment_variables", {})},
    )
    print(f"  [deploy] created function: {function_name}  handler={handler}")

    _wait_for_function_active(aws_clients["lambda"], function_name)

    # ------------------------------------------------------------------
    # 3. Invoke Lambda
    # ------------------------------------------------------------------
    payload_str = json.dumps(test_case["trigger_event"])
    response = aws_clients["lambda"].invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=payload_str,
    )
    raw_payload = response["Payload"].read()
    response_payload = json.loads(raw_payload)
    function_error    = response.get("FunctionError")  # "Handled" | "Unhandled" | None

    print(f"  [invoke] FunctionError={function_error!r}")
    print(f"  [invoke] Response: {json.dumps(response_payload, indent=4)}")

    # ------------------------------------------------------------------
    # 4. Assertion helpers
    # ------------------------------------------------------------------
    failures: list[str] = []

    def _fail(msg: str) -> None:
        failures.append(msg)
        print(f"  [FAIL] {msg}")

    validations = test_case.get("validations", {})

    # --- 4a. FunctionError check -------------------------------------------
    expected_function_error = validations.get("expected_function_error")
    if expected_function_error is not None:
        # Test case EXPECTS a FunctionError
        if function_error is None:
            _fail(
                f"Expected FunctionError={expected_function_error!r} "
                f"but Lambda returned no error."
            )
    else:
        # By default we do NOT expect a FunctionError
        if function_error:
            error_msg = response_payload.get("errorMessage", raw_payload.decode())
            _fail(f"Lambda raised FunctionError={function_error!r}: {error_msg}")

    # --- 4b. HTTP response validation --------------------------------------
    if "lambda_response" in validations:
        expected = validations["lambda_response"]

        if "statusCode" in expected:
            actual_sc = response_payload.get("statusCode")
            if actual_sc != expected["statusCode"]:
                _fail(
                    f"statusCode mismatch: expected {expected['statusCode']}, "
                    f"got {actual_sc}"
                )

        if "body" in expected:
            actual_body = response_payload.get("body")
            exp_body    = expected["body"]
            if actual_body != exp_body:
                _fail(f"body mismatch:\n  expected: {exp_body!r}\n  actual  : {actual_body!r}")

        if "body_contains" in expected:
            actual_body = response_payload.get("body", "")
            fragment    = expected["body_contains"]
            if fragment not in str(actual_body):
                _fail(
                    f"body_contains not found: {fragment!r} "
                    f"not in {actual_body!r}"
                )

    # --- 4c. Chime / raw JSON response validation --------------------------
    # Validates top-level keys in the response payload directly (exact match
    # per key).  Designed for Chime SMA handlers that return
    # ``{"SchemaVersion": "1.0", "Actions": [...]}`` rather than the HTTP
    # ``{"statusCode": 200, "body": "..."}`` shape used by API Gateway handlers.
    if "response_json" in validations:
        for rj_key, rj_expected in validations["response_json"].items():
            rj_actual = response_payload.get(rj_key)
            if rj_actual != rj_expected:
                _fail(
                    f"response_json['{rj_key}']: expected {rj_expected!r}, "
                    f"got {rj_actual!r}"
                )

    # --- 4d. First Action type check --------------------------------------
    # Validates the ``Type`` field of ``Actions[0]`` in the response payload.
    # Designed for Chime SMA responses whose ``Actions`` array drives the call.
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

    # --- 4e. DynamoDB item validation --------------------------------------
    if "dynamodb_item" in validations:
        ddb_val  = validations["dynamodb_item"]
        table    = ddb_val["TableName"]
        key      = ddb_val["Key"]
        expected_attrs = ddb_val.get("ExpectedAttributeValue", {})

        try:
            item_resp = aws_clients["dynamodb"].get_item(TableName=table, Key=key)
            item = item_resp.get("Item")
            if item is None:
                _fail(f"DynamoDB: item not found in table={table} key={key}")
            else:
                for attr_name, attr_val in expected_attrs.items():
                    actual_val = item.get(attr_name)
                    if actual_val != attr_val:
                        _fail(
                            f"DynamoDB attr mismatch [{attr_name}]: "
                            f"expected={attr_val!r}, actual={actual_val!r}"
                        )
        except ClientError as exc:
            _fail(f"DynamoDB get_item error: {exc}")

    # --- 4f. S3 object validation ------------------------------------------
    if "s3_object" in validations:
        s3_val  = validations["s3_object"]
        bucket  = s3_val["Bucket"]
        key     = s3_val["Key"]
        expected_content = s3_val.get("ExpectedContent")
        expected_contains = s3_val.get("ContentContains")

        try:
            obj = aws_clients["s3"].get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read().decode("utf-8")
            print(f"  [s3] s3://{bucket}/{key} content: {body!r}")
            if expected_content is not None and body != expected_content:
                _fail(
                    f"S3 content mismatch for s3://{bucket}/{key}:\n"
                    f"  expected: {expected_content!r}\n"
                    f"  actual  : {body!r}"
                )
            if expected_contains and expected_contains not in body:
                _fail(
                    f"S3 content_contains not found: {expected_contains!r} "
                    f"not in s3://{bucket}/{key}"
                )
        except ClientError as exc:
            _fail(f"S3 get_object error for s3://{bucket}/{key}: {exc}")

    # ------------------------------------------------------------------
    # 5. Report result
    # ------------------------------------------------------------------
    if failures:
        pytest.fail(
            f"Test case '{name}' failed with {len(failures)} assertion(s):\n"
            + "\n".join(f"  • {f}" for f in failures)
        )

    print(f"  [PASS] {name}")

