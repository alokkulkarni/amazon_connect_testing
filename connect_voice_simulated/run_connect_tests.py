#!/usr/bin/env python3
"""
run_connect_tests.py
====================
Amazon Connect Dashboard Test Runner
--------------------------------------
Executes test cases created in the Amazon Connect Test Desk (dashboard)
using the following Amazon Connect APIs:

  • StartTestCaseExecution            – kick off a single test case run
  • GetTestCaseExecutionSummary        – poll for status + ObservationSummary (INITIATED|IN_PROGRESS|PASSED|FAILED|STOPPED)
  • ListTestCaseExecutionRecords       – retrieve per-observation execution records on completion

Key capabilities
----------------
  * Concurrent execution of up to N tests (default 5, configurable)
  * IAM role assumption via STS (used by GitHub Actions OIDC)
  * Structured JSON results with per-step transcript data
  * Delegates report generation to report_generator.py
  * All runtime parameters read from config.json (with ENV-VAR overrides)
  * Full CLI interface with --config, --suite, --test, --output-dir flags
  * Retry logic on transient API failures
  * Detailed logging to both stdout and a log file

Usage
-----
  python run_connect_tests.py [OPTIONS]

  Options:
    --config PATH           Path to config.json (default: ./config.json)
    --suite SUITE_ID        Run only this suite (repeatable)
    --test  TEST_CASE_ID    Run only this test-case ID (repeatable)
    --output-dir DIR        Override output directory from config
    --concurrency N         Override max concurrent tests from config
    --dry-run               Print the execution plan without calling APIs
    --log-level LEVEL       DEBUG | INFO | WARNING | ERROR

Environment variables (all optional – override config.json values)
------------------------------------------------------------------
  AWS_REGION              AWS region (default: eu-west-2)
  CONNECT_INSTANCE_ID     Amazon Connect instance UUID
  CONNECT_INSTANCE_ALIAS  Amazon Connect instance alias
  AWS_ROLE_ARN            IAM role ARN to assume (OIDC / CI)
  AWS_ROLE_SESSION_NAME   STS session name
  CONNECT_OUTPUT_DIR      Output directory for reports
  CONNECT_CONCURRENCY     Max concurrent test executions

.env file
---------
  All variables above can also be placed in a .env file located next to this
  script (connect_voice_simulated/.env).  python-dotenv loads it automatically
  at startup; shell/CI variables set before the process starts always win
  (override=False).
"""

import os
import sys
import json
import time
import uuid
import logging
import argparse
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Botocore millisecond-timestamp monkey-patch
# ---------------------------------------------------------------------------
# Amazon Connect Testing APIs (StartTestCaseExecution,
# GetTestCaseExecutionSummary, ListTestCaseExecutionRecords) return epoch
# timestamps in MILLISECONDS, while botocore expects SECONDS.
# Passing a value like 1_740_000_000_000 (ms) to datetime.fromtimestamp()
# as seconds resolves to year ~58 000 and raises:
#   ValueError: year must be in 1..9999, not 58123
#
# Fix: wrap the private helper that performs the conversion and divide by
# 1 000 whenever the value is clearly in milliseconds (> 1e10 sec ≈ year 2286).
# ---------------------------------------------------------------------------
import botocore.utils as _bcu

_orig_parse_ts_tz = _bcu._parse_timestamp_with_tzinfo  # type: ignore[attr-defined]


def _safe_parse_timestamp_with_tzinfo(value, tzinfo):
    """Convert millisecond epoch values to seconds before datetime parsing."""
    if isinstance(value, (int, float)) and value > 1e10:
        value = value / 1000.0
    return _orig_parse_ts_tz(value, tzinfo)


_bcu._parse_timestamp_with_tzinfo = _safe_parse_timestamp_with_tzinfo  # type: ignore[attr-defined]
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------
# report_generator lives in the same directory
_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_DIR))
import report_generator  # noqa: E402

# ---------------------------------------------------------------------------
# Logging bootstrap (format updated once --log-level is parsed)
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(threadName)-20s  %(message)s"
logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO)
log = logging.getLogger("connect_test_runner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Terminal states as returned by ListTestCaseExecutions (TestCaseExecutionStatus)
TERMINAL_STATES = {"PASSED", "FAILED", "STOPPED"}

# Status codes that warrant a retry attempt
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# ===========================================================================
# Configuration helpers
# ===========================================================================

def _env_or(key: str, default: str) -> str:
    """Return the value of environment variable *key*, or *default* if unset/empty."""
    return os.environ.get(key) or default


def load_config(config_path: str) -> dict:
    """
    Load and validate configuration from *config_path*.

    Environment variables take precedence over the JSON file values.
    Placeholder tokens like ``${SOME_VAR}`` are resolved against ``os.environ``.

    Parameters
    ----------
    config_path : str
        Absolute or relative path to config.json.

    Returns
    -------
    dict
        Fully resolved configuration dictionary.
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, encoding="utf-8") as fh:
        raw = fh.read()

    # Resolve ${VAR:-default} and ${VAR} tokens
    import re
    def _replace(match: re.Match) -> str:
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var.strip(), default.strip())
        return os.environ.get(expr.strip(), match.group(0))  # leave as-is if missing

    raw = re.sub(r"\$\{([^}]+)\}", _replace, raw)
    cfg = json.loads(raw)

    # Environment-variable overrides (highest priority)
    cfg["connect"]["instance_id"]    = _env_or("CONNECT_INSTANCE_ID",    cfg["connect"]["instance_id"])
    cfg["connect"]["instance_alias"] = _env_or("CONNECT_INSTANCE_ALIAS", cfg["connect"].get("instance_alias", ""))
    cfg["connect"]["region"]         = _env_or("AWS_REGION",             cfg["connect"]["region"])
    cfg["iam"]["role_arn"]           = _env_or("AWS_ROLE_ARN",           cfg["iam"]["role_arn"])
    cfg["iam"]["session_name"]       = _env_or("AWS_ROLE_SESSION_NAME",  cfg["iam"]["session_name"])

    # Validate mandatory fields
    mandatory = {"connect.instance_id"}
    for dotted in mandatory:
        parts = dotted.split(".")
        val = cfg
        for p in parts:
            val = val.get(p, "")
        if not val or val.startswith("${"):
            raise ValueError(
                f"Missing required config value '{dotted}'. "
                f"Set it in config.json or via environment variable."
            )

    log.debug("Configuration loaded from %s", path)
    return cfg


def load_test_cases(tc_path: str) -> list[dict]:
    """
    Load test cases from *tc_path* and return a flat list of test case dicts,
    each enriched with ``suite_id`` and ``suite_name`` fields.

    Parameters
    ----------
    tc_path : str
        Path to test_cases.json.

    Returns
    -------
    list[dict]
        Flat list of test case dicts.
    """
    path = Path(tc_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Test cases file not found: {path}")

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    flat = []
    for suite in data.get("test_suites", []):
        suite_id   = suite.get("suite_id", "UNKNOWN")
        suite_name = suite.get("suite_name", "Unnamed Suite")
        for tc in suite.get("test_cases", []):
            tc = dict(tc)
            tc["suite_id"]   = suite_id
            tc["suite_name"] = suite_name
            flat.append(tc)

    log.info("Loaded %d test case(s) from %s", len(flat), path)
    return flat


# ===========================================================================
# AWS client factory
# ===========================================================================

def build_connect_client(cfg: dict):
    """
    Build and return a ``boto3`` Amazon Connect client.

    If ``iam.role_arn`` is non-empty (and does not contain an unresolved
    placeholder), the runner assumes that role via STS before creating the
    client.  This supports GitHub Actions OIDC-based role assumption.

    Parameters
    ----------
    cfg : dict
        Loaded configuration dictionary.

    Returns
    -------
    botocore.client.Connect
    """
    region    = cfg["connect"]["region"]
    role_arn  = cfg["iam"]["role_arn"]
    sess_name = cfg["iam"]["session_name"]
    duration  = int(cfg["iam"]["session_duration_seconds"])

    # Determine whether we should assume a role
    should_assume = (
        role_arn
        and not role_arn.startswith("${")
        and "arn:aws:iam" in role_arn
    )

    if should_assume:
        log.info("Assuming IAM role: %s (session: %s)", role_arn, sess_name)
        sts = boto3.client("sts", region_name=region)
        resp = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=sess_name,
            DurationSeconds=duration,
        )
        creds = resp["Credentials"]
        session = boto3.Session(
            aws_access_key_id     = creds["AccessKeyId"],
            aws_secret_access_key = creds["SecretAccessKey"],
            aws_session_token     = creds["SessionToken"],
            region_name           = region,
        )
        log.info("Successfully assumed role, temporary credentials obtained.")
    else:
        log.info(
            "No IAM role ARN configured – using ambient credentials (env vars / instance profile)."
        )
        session = boto3.Session(region_name=region)

    return session.client("connect")


# ===========================================================================
# API wrappers with retry logic
# ===========================================================================

def _call_with_retry(fn, max_attempts: int, delay_seconds: int, *args, **kwargs) -> Any:
    """
    Call *fn* with *args* / *kwargs*, retrying up to *max_attempts* times on
    transient errors (HTTP 429, 5xx).

    Parameters
    ----------
    fn            : callable – boto3 client method
    max_attempts  : int
    delay_seconds : int – base delay between retries (doubles on each attempt)
    *args, **kwargs : forwarded to *fn*

    Returns
    -------
    Any – the return value from *fn*

    Raises
    ------
    ClientError – on non-retryable errors or once all attempts are exhausted.
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except ClientError as exc:
            code = int(exc.response["ResponseMetadata"]["HTTPStatusCode"])
            if code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
                wait = delay_seconds * (2 ** (attempt - 1))
                log.warning(
                    "Transient error (HTTP %d) on attempt %d/%d – retrying in %ds …",
                    code, attempt, max_attempts, wait,
                )
                time.sleep(wait)
                last_exc = exc
            else:
                raise
        except (BotoCoreError, Exception) as exc:
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(delay_seconds)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def start_test_case_execution(client, instance_id: str, test_case_id: str) -> str:
    """
    Call ``StartTestCaseExecution`` for a single test case.

    Parameters
    ----------
    client        : boto3 Amazon Connect client
    instance_id   : str  – Connect instance UUID
    test_case_id  : str  – dashboard test case ID

    Returns
    -------
    str : ``TestCaseExecutionId`` assigned by Connect
    """
    log.info("Starting test case execution: %s", test_case_id)
    resp = _call_with_retry(
        client.start_test_case_execution,
        max_attempts=3,
        delay_seconds=5,
        InstanceId=instance_id,
        TestCaseId=test_case_id,
        ClientToken=str(uuid.uuid4()),
    )
    exec_id = resp["TestCaseExecutionId"]
    log.info("  → TestCaseExecutionId: %s", exec_id)
    return exec_id


def get_execution_records(client, instance_id: str, test_case_id: str, exec_id: str) -> list:
    """
    Call ``ListTestCaseExecutionRecords`` to retrieve all per-observation
    execution records for a completed (terminal-state) execution.
    Paginates automatically and returns the flattened list.

    Parameters
    ----------
    client        : boto3 Amazon Connect client
    instance_id   : str
    test_case_id  : str  – dashboard test case ID
    exec_id       : str  – execution ID returned by StartTestCaseExecution

    Returns
    -------
    list[dict] : combined ExecutionRecords from all pages
    """
    records = []
    kwargs = {
        "InstanceId":          instance_id,
        "TestCaseId":          test_case_id,
        "TestCaseExecutionId": exec_id,
    }
    while True:
        resp = _call_with_retry(
            client.list_test_case_execution_records,
            max_attempts=3,
            delay_seconds=5,
            **kwargs,
        )
        records.extend(resp.get("ExecutionRecords", []))
        next_token = resp.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token
    return records


def get_execution_summary(client, instance_id: str, test_case_id: str, exec_id: str) -> dict:
    """
    Call ``GetTestCaseExecutionSummary`` to retrieve the current status,
    start/end time, and observation counts for an in-progress or completed
    test case execution.

    Parameters
    ----------
    client        : boto3 Amazon Connect client
    instance_id   : str
    test_case_id  : str  – dashboard test case ID
    exec_id       : str  – execution ID returned by StartTestCaseExecution

    Returns
    -------
    dict
        Raw ``GetTestCaseExecutionSummaryResponse``:
        {
            "Status"            : INITIATED | IN_PROGRESS | PASSED | FAILED | STOPPED
            "StartTime"         : datetime | None
            "EndTime"           : datetime | None
            "ObservationSummary": {
                "TotalObservations"  : int,
                "ObservationsPassed" : int,
                "ObservationsFailed" : int,
            }
        }
    """
    return _call_with_retry(
        client.get_test_case_execution_summary,
        max_attempts=3,
        delay_seconds=5,
        InstanceId=instance_id,
        TestCaseId=test_case_id,
        TestCaseExecutionId=exec_id,
    )


# ===========================================================================
# Polling loop
# ===========================================================================

def poll_until_complete(
    client,
    instance_id: str,
    test_case_id: str,
    exec_id: str,
    poll_interval: int,
    max_attempts: int,
) -> tuple[str, dict, list]:
    """
    Repeatedly call ``GetTestCaseExecutionSummary`` until the execution reaches
    a terminal state (PASSED | FAILED | STOPPED) or the poll budget is
    exhausted.  On reaching a terminal state, fetches full per-observation
    records via ``ListTestCaseExecutionRecords``.

    Parameters
    ----------
    client        : boto3 Amazon Connect client
    instance_id   : str
    test_case_id  : str  – dashboard test case ID
    exec_id       : str
    poll_interval : int  – seconds between polls
    max_attempts  : int  – maximum number of poll attempts

    Returns
    -------
    tuple[str, dict, list]
        (final_status, summary_response, execution_records_list)
    """
    log.info("Polling execution %s …", exec_id)
    summary_resp: dict = {}
    for attempt in range(1, max_attempts + 1):
        summary_resp = get_execution_summary(client, instance_id, test_case_id, exec_id)
        status = summary_resp.get("Status", "UNKNOWN")
        obs = summary_resp.get("ObservationSummary", {})
        log.debug(
            "  [%d/%d]  status=%s  passed=%s  failed=%s",
            attempt, max_attempts, status,
            obs.get("ObservationsPassed"), obs.get("ObservationsFailed"),
        )

        if status in TERMINAL_STATES:
            log.info("Execution %s reached terminal state: %s", exec_id, status)
            records = get_execution_records(client, instance_id, test_case_id, exec_id)
            return status, summary_resp, records

        time.sleep(poll_interval)

    log.warning("Execution %s did not complete within %d polls.", exec_id, max_attempts)
    # Fetch whatever records exist even if we timed out
    records = get_execution_records(client, instance_id, test_case_id, exec_id)
    return summary_resp.get("Status", "STOPPED"), summary_resp, records


# ===========================================================================
# Single test runner (executed in a thread)
# ===========================================================================

def run_single_test(
    client,
    instance_id: str,
    test_case: dict,
    cfg: dict,
    results_lock: threading.Lock,
    results_list: list,
    dry_run: bool = False,
) -> None:
    """
    Execute a single test case end-to-end and append its result to
    *results_list* in a thread-safe manner.

    Called by the thread-pool executor; exceptions are caught and recorded
    as ERROR results rather than propagating.

    Parameters
    ----------
    client        : boto3 Amazon Connect client
    instance_id   : str
    test_case     : dict  – single test case from test_cases.json (enriched)
    cfg           : dict  – loaded config
    results_lock  : threading.Lock
    results_list  : list  – shared result accumulator
    dry_run       : bool  – when True, no API calls are made
    """
    tc_id   = test_case["test_case_id"]
    tc_name = test_case["name"]
    suite   = test_case.get("suite_name", "Unknown Suite")

    # Build a result skeleton that is always populated regardless of outcome
    result = {
        "test_case_id":   tc_id,
        "test_case_name": tc_name,
        "suite_id":       test_case.get("suite_id"),
        "suite_name":     suite,
        "status":         "UNKNOWN",
        "started_at":     datetime.now(timezone.utc).isoformat(),
        "finished_at":    None,
        "execution_id":   None,
        "summary":        {},
        "steps":          [],
        "error":          None,
        "tags":           test_case.get("tags", []),
    }

    if dry_run:
        log.info("[DRY RUN] Would execute test: %s (%s)", tc_name, tc_id)
        result["status"] = "SKIPPED"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
    else:
        retry_cfg     = cfg["execution"]
        max_retries   = int(retry_cfg["retry_attempts"]) if retry_cfg["retry_on_failure"] else 0
        retry_delay   = int(retry_cfg["retry_delay_seconds"])
        poll_interval = int(retry_cfg["poll_interval_seconds"])

        # Per-test timeout_seconds overrides the global max_poll_attempts when present.
        # e.g. timeout_seconds=180, poll_interval=5  →  max_polls=36
        tc_timeout = test_case.get("timeout_seconds")
        if tc_timeout:
            max_polls = max(1, int(tc_timeout) // poll_interval)
            log.debug("Test %s: using per-test timeout %ds → %d poll attempts",
                      tc_id, tc_timeout, max_polls)
        else:
            max_polls = int(retry_cfg["max_poll_attempts"])

        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    log.info("Retrying test %s (attempt %d/%d) …", tc_id, attempt + 1, max_retries + 1)
                    time.sleep(retry_delay)

                # ── Step 1: Start the execution ────────────────────────────
                # Errors here are retriable (new execution is safe to start).
                try:
                    exec_id = start_test_case_execution(client, instance_id, tc_id)
                    result["execution_id"] = exec_id
                except (ClientError, BotoCoreError) as exc:
                    last_exc = exc
                    log.error("API error starting test %s: %s", tc_id, exc)
                    result["error"]  = str(exc)
                    result["status"] = "ERROR"
                    continue  # retry: safe to start a new execution
                except Exception as exc:
                    last_exc = exc
                    log.error(
                        "Unexpected error starting test %s: %s\n%s",
                        tc_id, exc, traceback.format_exc(),
                    )
                    result["error"]  = str(exc)
                    result["status"] = "ERROR"
                    continue  # retry: safe to start a new execution

                # ── Step 2: Poll until done ────────────────────────────────
                # Errors here are NOT retriable with a fresh execution:
                # the test is already running on Connect; starting another one
                # would leave an orphaned IN_PROGRESS execution on the console.
                try:
                    status, summary, records = poll_until_complete(
                        client, instance_id, tc_id, exec_id, poll_interval, max_polls
                    )
                except (ClientError, BotoCoreError) as exc:
                    last_exc = exc
                    log.error(
                        "API error while polling test %s (exec %s): %s "
                        "— not retrying with a new execution.",
                        tc_id, exec_id, exc,
                    )
                    result["error"]  = str(exc)
                    result["status"] = "ERROR"
                    break  # do NOT create another execution
                except Exception as exc:
                    last_exc = exc
                    log.error(
                        "Unexpected error while polling test %s (exec %s): %s\n%s"
                        " — not retrying with a new execution.",
                        tc_id, exec_id, exc, traceback.format_exc(),
                    )
                    result["error"]  = str(exc)
                    result["status"] = "ERROR"
                    break  # do NOT create another execution

                result["status"]  = status
                result["summary"] = _normalise_summary(summary)
                result["steps"]   = _normalise_steps(records)
                break  # success – stop retry loop

            except Exception as exc:  # safety net – should not normally be reached
                last_exc = exc
                log.error(
                    "Unhandled error for test %s: %s\n%s",
                    tc_id, exc, traceback.format_exc(),
                )
                result["error"]  = str(exc)
                result["status"] = "ERROR"

        if result["status"] == "ERROR" and last_exc:
            log.error("Test %s ultimately failed after %d attempt(s).", tc_id, max_retries + 1)

    result["finished_at"] = datetime.now(timezone.utc).isoformat()

    with results_lock:
        results_list.append(result)
    log.info("Test completed: %-50s  STATUS=%s", tc_name, result["status"])


# ===========================================================================
# Response normalisation helpers
# ===========================================================================

def _normalise_summary(raw: dict) -> dict:
    """
    Extract a clean, JSON-serialisable summary from the raw
    ``GetTestCaseExecutionSummary`` API response.

    Response shape::

        {
            "Status"            : str,
            "StartTime"         : datetime | None,
            "EndTime"           : datetime | None,
            "ObservationSummary": {
                "TotalObservations"  : int,
                "ObservationsPassed" : int,
                "ObservationsFailed" : int,
            }
        }

    Parameters
    ----------
    raw : dict  – raw boto3 GetTestCaseExecutionSummaryResponse

    Returns
    -------
    dict
    """
    if not raw:
        return {}
    obs = raw.get("ObservationSummary") or {}
    return {
        "status":               raw.get("Status"),
        "start_time":           _ts(raw.get("StartTime")),
        "end_time":             _ts(raw.get("EndTime")),
        "total_observations":   obs.get("TotalObservations"),
        "observations_passed":  obs.get("ObservationsPassed"),
        "observations_failed":  obs.get("ObservationsFailed"),
    }


def _normalise_steps(records: list) -> list[dict]:
    """
    Convert the ``ExecutionRecords`` list returned by
    ``ListTestCaseExecutionRecords`` into a clean, JSON-serialisable list.

    Each ``ExecutionRecord`` has:
      - ``ObservationId`` : unique observation UUID
      - ``Status``        : PASSED | FAILED | IN_PROGRESS | STOPPED
      - ``Timestamp``     : datetime of the observation
      - ``Record``        : JSON string with block/step detail

    Parameters
    ----------
    records : list  – raw ExecutionRecords list from ListTestCaseExecutionRecords

    Returns
    -------
    list[dict]
    """
    if not records:
        return []
    steps = []
    for idx, rec in enumerate(records):
        # Parse the JSON-string Record field if present
        record_detail: dict = {}
        raw_record = rec.get("Record")
        if raw_record:
            try:
                record_detail = json.loads(raw_record)
            except (json.JSONDecodeError, TypeError):
                record_detail = {"raw": raw_record}
        steps.append({
            "observation_id": rec.get("ObservationId"),
            "status":         rec.get("Status"),
            "timestamp":      _ts(rec.get("Timestamp")),
            "record":         record_detail,
        })
    return steps


def _ts(value) -> str | None:
    """Convert a datetime object or an epoch integer to ISO-8601 string, or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    return str(value)


# ===========================================================================
# Orchestrator
# ===========================================================================

def run_all_tests(
    cfg: dict,
    test_cases: list[dict],
    output_dir: Path,
    dry_run: bool = False,
) -> dict:
    """
    Orchestrate concurrent test execution and collect results.

    Parameters
    ----------
    cfg        : dict       – loaded configuration
    test_cases : list[dict] – flat list of test case dicts
    output_dir : Path       – directory where reports are written
    dry_run    : bool

    Returns
    -------
    dict : complete run result payload (written to json report)
    """
    instance_id  = cfg["connect"]["instance_id"]
    max_workers  = int(cfg["execution"]["max_concurrent_tests"])
    run_id       = str(uuid.uuid4())
    started_at   = datetime.now(timezone.utc).isoformat()

    log.info("=" * 72)
    log.info("Connect Dashboard Test Runner")
    log.info("  Run ID          : %s", run_id)
    log.info("  Instance ID     : %s", instance_id)
    log.info("  Total test cases: %d", len(test_cases))
    log.info("  Concurrency     : %d", max_workers)
    log.info("  Output dir      : %s", output_dir)
    log.info("  Dry run         : %s", dry_run)
    log.info("=" * 72)

    client = None if dry_run else build_connect_client(cfg)

    results:      list  = []
    results_lock        = threading.Lock()

    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="connect-test"
    ) as pool:
        futures = {
            pool.submit(
                run_single_test,
                client,
                instance_id,
                tc,
                cfg,
                results_lock,
                results,
                dry_run,
            ): tc
            for tc in test_cases
        }

        for future in as_completed(futures):
            tc = futures[future]
            try:
                future.result()
            except Exception as exc:
                # Should not reach here – run_single_test swallows exceptions,
                # but guard just in case.
                log.error(
                    "Unexpected unhandled exception for test '%s': %s",
                    tc.get("name"), exc,
                )

    # ── Compute aggregate run statistics ────────────────────────────────────
    total   = len(results)
    passed  = sum(1 for r in results if r["status"] == "PASSED")
    failed  = sum(1 for r in results if r["status"] in {"FAILED", "ERROR"})
    skipped = sum(1 for r in results if r["status"] == "SKIPPED")
    timeout = sum(1 for r in results if r["status"] == "STOPPED")

    finished_at = datetime.now(timezone.utc).isoformat()

    run_payload = {
        "run_id":      run_id,
        "started_at":  started_at,
        "finished_at": finished_at,
        "instance_id": instance_id,
        "region":      cfg["connect"]["region"],
        "statistics": {
            "total":   total,
            "passed":  passed,
            "failed":  failed,
            "skipped": skipped,
            "timeout": timeout,
            "pass_rate": f"{(passed / total * 100):.1f}%" if total else "0%",
        },
        "results": sorted(results, key=lambda r: (r.get("suite_id") or "", r.get("test_case_id") or "")),
        "config_snapshot": {
            "concurrency":     max_workers,
            "poll_interval":   cfg["execution"]["poll_interval_seconds"],
            "max_polls":       cfg["execution"]["max_poll_attempts"],
            "retry_enabled":   cfg["execution"]["retry_on_failure"],
            "retry_attempts":  cfg["execution"]["retry_attempts"],
        },
    }

    log.info("=" * 72)
    log.info(
        "Run complete. TOTAL=%d  PASS=%d  FAIL=%d  ERROR/TIMEOUT=%d  SKIP=%d",
        total, passed, failed, failed, skipped,
    )
    log.info("=" * 72)

    return run_payload


# ===========================================================================
# Output persistence
# ===========================================================================

def _merge_run_payloads(payloads: list[dict]) -> dict:
    """
    Merge multiple per-batch run payloads into one combined payload.

    The combined payload carries the run_id and started_at of the first batch,
    the finished_at of the last batch, and the sum of all per-batch statistics.
    All per-test results are concatenated in suite/test-case order.

    Parameters
    ----------
    payloads : list[dict]  – list of payloads returned by run_all_tests()

    Returns
    -------
    dict : a single run payload aggregating all batches
    """
    if not payloads:
        raise ValueError("Cannot merge an empty list of payloads")
    if len(payloads) == 1:
        return payloads[0]

    # Aggregate statistics
    total   = sum(p.get("statistics", {}).get("total",   0) for p in payloads)
    passed  = sum(p.get("statistics", {}).get("passed",  0) for p in payloads)
    failed  = sum(p.get("statistics", {}).get("failed",  0) for p in payloads)
    skipped = sum(p.get("statistics", {}).get("skipped", 0) for p in payloads)
    timeout = sum(p.get("statistics", {}).get("timeout", 0) for p in payloads)

    combined_stats = {
        "total":     total,
        "passed":    passed,
        "failed":    failed,
        "skipped":   skipped,
        "timeout":   timeout,
        "pass_rate": f"{(passed / total * 100):.1f}%" if total else "0%",
    }

    # Flatten all individual test results (key is "results" in the payload)
    all_results: list[dict] = []
    for p in payloads:
        all_results.extend(p.get("results", []))

    # Re-sort by suite then test-case id (mirrors run_all_tests sort)
    all_results.sort(key=lambda r: (r.get("suite_id") or "", r.get("test_case_id") or ""))

    base = payloads[0]
    return {
        "run_id":      base["run_id"],
        "started_at":  base["started_at"],
        "finished_at": payloads[-1]["finished_at"],
        "instance_id": base["instance_id"],
        "region":      base["region"],
        "statistics":  combined_stats,
        "results":     all_results,
        "config_snapshot": base.get("config_snapshot", {}),
    }


def persist_results(
    run_payload: dict,
    cfg: dict,
    output_dir: Path,
    name_suffix: str = "",
) -> dict[str, Path]:
    """
    Write JSON, HTML and XML reports to *output_dir* and return a mapping
    of format name → file path.

    Parameters
    ----------
    run_payload : dict  – complete run result from run_all_tests()
    cfg         : dict  – loaded configuration
    output_dir  : Path
    name_suffix : str   – appended to each report filename stem before the
                          extension (e.g. "_batch_01" → connect_test_results_batch_01.json)

    Returns
    -------
    dict[str, Path]
        e.g. {"json": Path("test-results/connect_test_results.json"), …}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rcfg = cfg["reporting"]

    def _suffixed(base_name: str) -> str:
        """Insert *name_suffix* before the file extension."""
        if not name_suffix:
            return base_name
        p = Path(base_name)
        return str(p.with_name(p.stem + name_suffix + p.suffix))

    paths: dict[str, Path] = {}

    # ── JSON ──────────────────────────────────────────────────────────────
    json_path = output_dir / _suffixed(rcfg["json_report_name"])
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(run_payload, fh, indent=2, default=str)
    paths["json"] = json_path
    log.info("JSON report written: %s", json_path)

    # ── HTML ──────────────────────────────────────────────────────────────
    html_path = output_dir / _suffixed(rcfg["html_report_name"])
    report_generator.write_html_report(run_payload, html_path, rcfg)
    paths["html"] = html_path
    log.info("HTML report written: %s", html_path)

    # ── XML (JUnit-compatible) ─────────────────────────────────────────────
    xml_path = output_dir / _suffixed(rcfg["xml_report_name"])
    report_generator.write_xml_report(run_payload, xml_path)
    paths["xml"] = xml_path
    log.info("XML report  written: %s", xml_path)

    return paths


# ===========================================================================
# CLI
# ===========================================================================

def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the CLI."""
    p = argparse.ArgumentParser(
        prog="run_connect_tests.py",
        description="Amazon Connect Dashboard Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--config", default=str(_DIR / "config.json"),
        help="Path to config.json (default: ./config.json)",
    )
    p.add_argument(
        "--test-cases", default=str(_DIR / "test_cases.json"),
        help="Path to test_cases.json (default: ./test_cases.json)",
    )
    p.add_argument(
        "--suite", action="append", dest="suites", metavar="SUITE_ID",
        help="Run only tests in this suite ID (repeatable)",
    )
    p.add_argument(
        "--test", action="append", dest="tests", metavar="TEST_CASE_ID",
        help="Run only this test case ID (repeatable)",
    )
    p.add_argument(
        "--output-dir", dest="output_dir",
        help="Override the output directory from config",
    )
    p.add_argument(
        "--concurrency", type=int, dest="concurrency",
        help="Override max concurrent tests (default: from config)",
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print execution plan without calling APIs",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return p


def main() -> int:
    """
    Entry point.

    Returns
    -------
    int : 0 on success (all tests pass), 1 if any tests failed/errored.
    """
    parser = _build_arg_parser()
    args   = parser.parse_args()

    # ── Adjust log level ──────────────────────────────────────────────────
    numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(numeric_level)
    log.setLevel(numeric_level)

    # ── Load .env (does not overwrite existing shell/CI variables) ────────
    _env_file = _DIR / ".env"
    if _env_file.exists():
        loaded = load_dotenv(dotenv_path=_env_file, override=False)
        log.debug(".env file %s (%s)", _env_file, "loaded" if loaded else "already set – skipped")
    else:
        log.debug("No .env file found at %s – using shell / CI environment only", _env_file)

    # ── Load config ───────────────────────────────────────────────────────
    cfg = load_config(args.config)

    # ── Output directory (CLI flag > env var > config) ────────────────────
    out_dir_str = (
        args.output_dir
        or os.environ.get("CONNECT_OUTPUT_DIR")
        or cfg["reporting"]["output_dir"]
    )
    # Create a timestamped run subdirectory so successive runs never
    # overwrite each other:
    #   <base>/test-results/2026-02-25_14-28-53/
    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = Path(out_dir_str) / run_timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Run output dir : %s", output_dir)

    # ── Log file ──────────────────────────────────────────────────────────
    if cfg["logging"]["log_to_file"]:
        file_handler = logging.FileHandler(output_dir / cfg["logging"]["log_file_name"])
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)

    # ── Concurrency override ──────────────────────────────────────────────
    if args.concurrency:
        cfg["execution"]["max_concurrent_tests"] = args.concurrency
    concurrency_env = os.environ.get("CONNECT_CONCURRENCY")
    if concurrency_env:
        cfg["execution"]["max_concurrent_tests"] = int(concurrency_env)

    # ── Load and filter test cases ────────────────────────────────────────
    all_test_cases = load_test_cases(args.test_cases)

    selected: list[dict] = []
    if args.tests:
        ids = set(args.tests)
        selected = [tc for tc in all_test_cases if tc["test_case_id"] in ids]
    elif args.suites:
        ids = set(args.suites)
        selected = [tc for tc in all_test_cases if tc["suite_id"] in ids]
    else:
        selected = all_test_cases

    if not selected:
        log.error("No test cases matched the provided filter. Exiting.")
        return 1

    # Honour test_selection.test_case_ids from config when run_mode != 'all'
    run_mode = cfg["test_selection"]["run_mode"]
    cfg_ids  = cfg["test_selection"]["test_case_ids"]
    if run_mode != "all" and cfg_ids:
        ids     = set(cfg_ids)
        selected = [tc for tc in selected if tc["test_case_id"] in ids]
        log.info("Filtered to %d test case(s) based on config.test_selection.", len(selected))

    log.info("Running %d test case(s).", len(selected))

    # ── Execute in batches ────────────────────────────────────────────────
    #
    # Tests are processed in sequential batches.  Within each batch every
    # test runs concurrently (up to max_concurrent_tests workers).  After
    # every batch a dedicated report is written so results are available
    # immediately without waiting for the entire suite to finish.
    #
    # Example: 23 tests, concurrency 5 → 5 batches
    #   Batch 01: tests  1-5   (5 concurrent) → batch report written
    #   Batch 02: tests  6-10  (5 concurrent) → batch report written
    #   Batch 03: tests 11-15  (5 concurrent) → batch report written
    #   Batch 04: tests 16-20  (5 concurrent) → batch report written
    #   Batch 05: tests 21-23  (3 concurrent) → batch report written
    #   → Final combined report written
    #
    batch_size   = int(cfg["execution"]["max_concurrent_tests"])
    batches      = [selected[i:i + batch_size] for i in range(0, len(selected), batch_size)]
    total_batches = len(batches)

    log.info("Batched execution: %d test(s) across %d batch(es) of up to %d concurrent",
             len(selected), total_batches, batch_size)

    batch_payloads: list[dict] = []

    for batch_num, batch_tests in enumerate(batches, start=1):
        log.info("─" * 72)
        log.info("Batch %d / %d  (%d test(s))",
                 batch_num, total_batches, len(batch_tests))
        log.info("─" * 72)

        batch_payload = run_all_tests(cfg, batch_tests, output_dir, dry_run=args.dry_run)
        batch_payloads.append(batch_payload)

        # Write per-batch report immediately
        suffix = f"_batch_{batch_num:02d}"
        batch_paths = persist_results(batch_payload, cfg, output_dir, name_suffix=suffix)

        bs = batch_payload["statistics"]
        log.info(
            "Batch %d / %d  ✓ done  — passed=%d  failed=%d  stopped=%d  "
            "(HTML: %s)",
            batch_num, total_batches,
            bs.get("passed", 0), bs.get("failed", 0), bs.get("timeout", 0),
            batch_paths.get("html", ""),
        )

    # ── Merge all batches and write the final combined report ─────────────
    run_payload  = _merge_run_payloads(batch_payloads)
    report_paths = persist_results(run_payload, cfg, output_dir)

    # ── Print a console results table ────────────────────────────────────
    _print_console_table(run_payload)

    # ── Return non-zero exit code if any tests failed ─────────────────────
    stats = run_payload["statistics"]
    if stats["failed"] > 0 or stats["timeout"] > 0:
        return 1
    return 0


def _print_console_table(run_payload: dict) -> None:
    """
    Print a clean ANSI-formatted results table to stdout.
    Falls back to plain text when the terminal does not support colour.
    """
    import shutil

    # ── ANSI helpers ───────────────────────────────────────────────────────
    IS_TTY = sys.stdout.isatty()
    def _c(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if IS_TTY else text

    BOLD   = lambda t: _c("1",    t)
    DIM    = lambda t: _c("2",    t)
    GREEN  = lambda t: _c("32",   t)
    RED    = lambda t: _c("31",   t)
    YELLOW = lambda t: _c("33",   t)
    CYAN   = lambda t: _c("36",   t)
    WHITE  = lambda t: _c("97",   t)

    STATUS_FMT = {
        "PASSED":  lambda s: GREEN(s),
        "FAILED":  lambda s: RED(s),
        "ERROR":   lambda s: RED(s),
        "STOPPED": lambda s: YELLOW(s),
        "SKIPPED": lambda s: DIM(s),
    }

    def fmt_status(s: str) -> str:
        return STATUS_FMT.get(s, lambda x: x)(s or "UNKNOWN")

    # ── Build rows ─────────────────────────────────────────────────────────
    results = run_payload.get("results", [])
    rows = []
    for r in results:
        # Duration from started_at / finished_at
        try:
            from datetime import datetime as _dt
            start = _dt.fromisoformat(r["started_at"])
            end   = _dt.fromisoformat(r["finished_at"])
            dur   = f"{int((end - start).total_seconds())}s"
        except Exception:
            dur = "–"

        error_snippet = ""
        if r.get("error"):
            # Take the most useful part: the last sentence after the last ':'
            msg = r["error"].split(":")[-1].strip()
            error_snippet = msg[:72] + ("…" if len(msg) > 72 else "")

        obs = r.get("summary", {})
        obs_str = (
            f"{obs.get('observations_passed', '?')}/{obs.get('total_observations', '?')}"
            if obs else "–"
        )

        rows.append({
            "suite":   r.get("suite_id", "–"),
            "id":      r.get("test_case_id", "–")[-8:],   # last 8 chars of UUID
            "name":    r.get("test_case_name", "–"),
            "status":  r.get("status", "UNKNOWN"),
            "obs":     obs_str,
            "dur":     dur,
            "error":   error_snippet,
        })

    # ── Column widths ──────────────────────────────────────────────────────
    term_w  = shutil.get_terminal_size((120, 40)).columns
    name_w  = min(52, max(20, term_w - 72))
    error_w = min(60, max(20, term_w - 74))

    COL_W = {
        "suite":  8,
        "id":     10,
        "name":   name_w,
        "status": 8,
        "obs":    8,
        "dur":    6,
        "error":  error_w,
    }

    def _trunc(text: str, width: int) -> str:
        if len(text) > width:
            return text[:width - 1] + "…"
        return text.ljust(width)

    def _row(r: dict, *, header: bool = False) -> str:
        cells = [
            _trunc(r["suite"],  COL_W["suite"]),
            _trunc(r["id"],     COL_W["id"]),
            _trunc(r["name"],   COL_W["name"]),
            _trunc(r.get("status", ""), COL_W["status"]) if header else \
                _trunc(fmt_status(r["status"]), COL_W["status"] + (10 if IS_TTY else 0)),
            _trunc(r["obs"],    COL_W["obs"]),
            _trunc(r["dur"],    COL_W["dur"]),
            _trunc(r.get("error", ""), COL_W["error"]),
        ]
        return "  " + "  ".join(cells)

    sep = "  " + "-" * (sum(COL_W.values()) + 2 * (len(COL_W) - 1))

    # ── Print ──────────────────────────────────────────────────────────────
    stats = run_payload.get("statistics", {})

    print()
    print(BOLD(WHITE("━" * min(term_w, 100))))
    print(BOLD(WHITE("  AMAZON CONNECT TEST RESULTS")))
    print(BOLD(WHITE(f"  Run ID : {run_payload.get('run_id', '–')}")))
    total_s   = str(stats.get('total', 0))
    passed_s  = str(stats.get('passed', 0))
    failed_s  = str(stats.get('failed', 0))
    timeout_s = str(stats.get('timeout', 0))
    skipped_s = str(stats.get('skipped', 0))
    rate_s    = stats.get('pass_rate', '0%')
    print(WHITE(
        f"  Total: {total_s}   "
        f"{GREEN('Passed')}: {passed_s}   "
        f"{RED('Failed')}: {failed_s}   "
        f"{YELLOW('Timeout')}: {timeout_s}   "
        f"Skipped: {skipped_s}   "
        f"Pass rate: {rate_s}"
    ))
    print(BOLD(WHITE("━" * min(term_w, 100))))
    print()

    hdr = _row({"suite": "SUITE", "id": "TC-ID(…)", "name": "TEST NAME",
                "status": "STATUS", "obs": "OBS", "dur": "DUR",
                "error": "ERROR SUMMARY"}, header=True)
    print(BOLD(hdr))
    print(sep)

    for r in rows:
        print(_row(r))

    print(sep)
    print()

    # ── Report paths summary ───────────────────────────────────────────────
    report_dir = run_payload.get("output_dir") or ""
    if report_dir:
        print(DIM(f"  Reports → {report_dir}"))
    print()


if __name__ == "__main__":
    sys.exit(main())
