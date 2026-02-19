#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_aws_regression.sh – Automated Lambda regression tests against a LIVE
#                         AWS test environment.  Designed to run locally or
#                         inside a CI pipeline (GitHub Actions, Jenkins, etc.).
#
# Usage (from any directory):
#   ./lambda_testing/run_aws_regression.sh
#   ./lambda_testing/run_aws_regression.sh --function my-test-lambda
#   ./lambda_testing/run_aws_regression.sh --test-cases /path/to/my_cases.json
#   ./lambda_testing/run_aws_regression.sh --prefix "myapp-test-" --filter "TC-001,TC-002"
#   ./lambda_testing/run_aws_regression.sh --profile my-aws-profile --region eu-west-1
#   ./lambda_testing/run_aws_regression.sh --role arn:aws:iam::123456789012:role/TestRole
#   ./lambda_testing/run_aws_regression.sh --report-dir /tmp/ci-reports
#   PYTEST_ARGS="-k TC-001 -x" ./lambda_testing/run_aws_regression.sh
#
# Options:
#   --test-cases FILE            Path to test-cases JSON (default: lambda_test_cases.json).
#                                Supports absolute and cwd-relative paths.
#                                Overrides env var LAMBDA_TEST_CASES_FILE.
#   --report-dir  DIR            Output directory for JSON + HTML reports.
#                                Overrides env var LAMBDA_REPORT_DIR.
#                                Default: <script_dir>/reports
#   --function    FUNCTION_NAME  Override LAMBDA_TARGET_FUNCTION
#   --prefix      PREFIX         Override LAMBDA_FUNCTION_PREFIX
#   --filter      "TC-001,TC-002" Override REGRESSION_TEST_FILTER
#   --profile     PROFILE_NAME  Override AWS_TEST_PROFILE
#   --region      REGION         Override AWS_TEST_REGION (default: us-east-1)
#   --role        ROLE_ARN       Override AWS_TEST_ROLE_ARN
#   --no-cleanup                 Set CLEANUP_RESOURCES=false
#   --deploy                     Set LAMBDA_DEPLOY_FOR_TEST=true
#   -h / --help                  Show this help
#
# Prerequisites:
#   pip install -r lambda_testing/requirements.txt
#   Valid AWS credentials accessible (profile, env vars, or IAM instance role).
#
# CI environment variables (all --flags have an env-var equivalent):
#   LAMBDA_TEST_CASES_FILE, LAMBDA_REPORT_DIR, AWS_TEST_REGION,
#   AWS_TEST_PROFILE, AWS_TEST_ROLE_ARN, LAMBDA_TARGET_FUNCTION,
#   LAMBDA_FUNCTION_PREFIX, REGRESSION_TEST_FILTER, CLEANUP_RESOURCES,
#   LAMBDA_DEPLOY_FOR_TEST, PYTEST_ARGS
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─────────────────────────────────────────────────────────────────────────────
# Load .env files (suite-local first, then repo root as fallback)
# ─────────────────────────────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "${SCRIPT_DIR}/.env"
  set +o allexport
  echo "[info] Loaded ${SCRIPT_DIR}/.env"
elif [ -f "${REPO_ROOT}/.env" ]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +o allexport
  echo "[info] Loaded ${REPO_ROOT}/.env (fallback)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Defaults (overridden by .env or CLI flags below)
# ─────────────────────────────────────────────────────────────────────────────
AWS_TEST_REGION="${AWS_TEST_REGION:-us-east-1}"
AWS_TEST_PROFILE="${AWS_TEST_PROFILE:-}"
AWS_TEST_ROLE_ARN="${AWS_TEST_ROLE_ARN:-}"
LAMBDA_TARGET_FUNCTION="${LAMBDA_TARGET_FUNCTION:-}"
LAMBDA_FUNCTION_PREFIX="${LAMBDA_FUNCTION_PREFIX:-}"
REGRESSION_TEST_FILTER="${REGRESSION_TEST_FILTER:-}"
CLEANUP_RESOURCES="${CLEANUP_RESOURCES:-true}"
LAMBDA_DEPLOY_FOR_TEST="${LAMBDA_DEPLOY_FOR_TEST:-false}"
# Paths – can be overridden by env vars or CLI flags.
# We keep them as empty strings here so we can detect "was it set explicitly?"
LAMBDA_TEST_CASES_FILE="${LAMBDA_TEST_CASES_FILE:-}"
LAMBDA_REPORT_DIR="${LAMBDA_REPORT_DIR:-}"

# ─────────────────────────────────────────────────────────────────────────────
# Parse CLI flags
# ─────────────────────────────────────────────────────────────────────────────
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --test-cases) LAMBDA_TEST_CASES_FILE="$2";   shift 2 ;;
    --report-dir) LAMBDA_REPORT_DIR="$2";        shift 2 ;;
    --function)   LAMBDA_TARGET_FUNCTION="$2";   shift 2 ;;
    --prefix)     LAMBDA_FUNCTION_PREFIX="$2";   shift 2 ;;
    --filter)     REGRESSION_TEST_FILTER="$2";   shift 2 ;;
    --profile)    AWS_TEST_PROFILE="$2";          shift 2 ;;
    --region)     AWS_TEST_REGION="$2";           shift 2 ;;
    --role)       AWS_TEST_ROLE_ARN="$2";         shift 2 ;;
    --no-cleanup) CLEANUP_RESOURCES="false";       shift   ;;
    --deploy)     LAMBDA_DEPLOY_FOR_TEST="true";   shift   ;;
    -h|--help)    SHOW_HELP=true;                  shift   ;;
    *) echo "[warn] Unknown flag: $1"; shift ;;
  esac
done

# Resolve paths to absolutes (relative to cwd, not script dir) so they
# work correctly when called from a CI workspace root.
# Uses Python for portability (realpath -m is GNU-only, not on macOS without coreutils).
_abspath() {
  python3 -c "import os,sys; print(os.path.abspath(sys.argv[1]))" "$1"
}
if [ -n "${LAMBDA_TEST_CASES_FILE}" ]; then
  LAMBDA_TEST_CASES_FILE="$(_abspath "${LAMBDA_TEST_CASES_FILE}")"
fi
if [ -n "${LAMBDA_REPORT_DIR}" ]; then
  LAMBDA_REPORT_DIR="$(_abspath "${LAMBDA_REPORT_DIR}")"
fi

# Effective report directory (used for mkdir and final summary)
REPORT_DIR="${LAMBDA_REPORT_DIR:-${SCRIPT_DIR}/reports}"

if [ "${SHOW_HELP}" = "true" ]; then
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \?//'
  exit 0
fi

# Export so Python picks them up
export AWS_TEST_REGION AWS_TEST_PROFILE AWS_TEST_ROLE_ARN
export LAMBDA_TARGET_FUNCTION LAMBDA_FUNCTION_PREFIX REGRESSION_TEST_FILTER
export CLEANUP_RESOURCES LAMBDA_DEPLOY_FOR_TEST
export LAMBDA_TEST_CASES_FILE LAMBDA_REPORT_DIR

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "┌──────────────────────────────────────────────────────────────────┐"
echo "│  Pre-flight checks                                               │"
echo "└──────────────────────────────────────────────────────────────────┘"

if ! command -v python3 &>/dev/null; then
  echo "[error] python3 not found. Install Python 3.9+."
  exit 1
fi
echo "  [ok] Python: $(python3 --version)"

if ! command -v pytest &>/dev/null; then
  echo "[error] pytest not found. Run: pip install -r ${SCRIPT_DIR}/requirements.txt"
  exit 1
fi
echo "  [ok] pytest: $(pytest --version 2>&1 | head -1)"

if ! command -v aws &>/dev/null; then
  echo "  [warn] AWS CLI not found – connectivity check will rely on boto3 only."
else
  echo "  [ok] AWS CLI: $(aws --version 2>&1)"
fi

# Verify boto3 can resolve credentials
echo ""
echo "  Validating AWS credentials …"
IDENTITY_CHECK=$(python3 - <<'PYEOF'
import boto3, os, sys
try:
    kwargs = {"region_name": os.environ.get("AWS_TEST_REGION","us-east-1")}
    profile = os.environ.get("AWS_TEST_PROFILE","")
    if profile:
        kwargs["profile_name"] = profile
    sess = boto3.Session(**kwargs)
    role = os.environ.get("AWS_TEST_ROLE_ARN","")
    if role:
        sts = sess.client("sts")
        creds = sts.assume_role(
            RoleArn=role,
            RoleSessionName="preflight-check"
        )["Credentials"]
        sess = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=kwargs["region_name"]
        )
    identity = sess.client("sts").get_caller_identity()
    print(f"Account={identity['Account']}  UserId={identity['UserId']}")
except Exception as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
)
CRED_EXIT=$?

if [ "${CRED_EXIT}" -ne 0 ]; then
  echo ""
  echo "  [error] AWS credential check failed. Check your .env file and ensure"
  echo "          AWS_TEST_PROFILE, AWS_TEST_ACCESS_KEY_ID / SECRET, or instance"
  echo "          role credentials are configured."
  exit 1
fi
echo "  [ok] AWS identity: ${IDENTITY_CHECK}"

# Optional: verify target function exists (only when a specific function is named
# and LAMBDA_DEPLOY_FOR_TEST is false)
if [ -n "${LAMBDA_TARGET_FUNCTION}" ] && [ "${LAMBDA_DEPLOY_FOR_TEST}" = "false" ]; then
  echo ""
  echo "  Checking Lambda function '${LAMBDA_TARGET_FUNCTION}' in ${AWS_TEST_REGION} …"
  FUNC_CHECK=$(python3 - <<PYEOF 2>&1 || true
import boto3, os
kwargs = {"region_name": os.environ.get("AWS_TEST_REGION","us-east-1")}
profile = os.environ.get("AWS_TEST_PROFILE","")
if profile:
    kwargs["profile_name"] = profile
sess = boto3.Session(**kwargs)
lam = sess.client("lambda", region_name=kwargs["region_name"])
fn  = os.environ["LAMBDA_TARGET_FUNCTION"]
r   = lam.get_function_configuration(FunctionName=fn)
print(f"Runtime={r['Runtime']}  State={r.get('State','?')}  LastModified={r.get('LastModified','?')[:19]}")
PYEOF
  )
  if echo "${FUNC_CHECK}" | grep -q "^Runtime="; then
    echo "  [ok] Function found: ${FUNC_CHECK}"
  else
    echo "  [warn] Could not confirm function '${LAMBDA_TARGET_FUNCTION}': ${FUNC_CHECK}"
    echo "         The test run will still proceed – tests will fail if the function is absent."
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Create report directory
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "${REPORT_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  AWS LAMBDA REGRESSION TESTS"
echo "================================================================"
echo "  Region          : ${AWS_TEST_REGION}"
echo "  Profile         : ${AWS_TEST_PROFILE:-<default chain>}"
echo "  Role ARN        : ${AWS_TEST_ROLE_ARN:-<none>}"
echo "  Target function : ${LAMBDA_TARGET_FUNCTION:-<per test case>}"
echo "  Function prefix : ${LAMBDA_FUNCTION_PREFIX:-<none>}"
echo "  Filter          : ${REGRESSION_TEST_FILTER:-<all test cases>}"
echo "  Deploy for test : ${LAMBDA_DEPLOY_FOR_TEST}"
echo "  Cleanup after   : ${CLEANUP_RESOURCES}"
  echo "  Test cases      : ${LAMBDA_TEST_CASES_FILE:-${SCRIPT_DIR}/lambda_test_cases.json}"
echo "  Reports dir     : ${REPORT_DIR}"
echo "================================================================"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Build pytest flags
# ─────────────────────────────────────────────────────────────────────────────
EXTRA_ARGS="${PYTEST_ARGS:-}"

HTML_ARGS=""
if python3 -c "import pytest_html" 2>/dev/null; then
  HTML_ARGS="--html=${REPORT_DIR}/aws_regression_report_pytest.html --self-contained-html"
else
  echo "[warn] pytest-html not installed – skipping pytest HTML report."
  echo "       The native HTML report (aws_regression_report.html) is always generated."
  echo "       To install: pip install pytest-html"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Run tests
# ─────────────────────────────────────────────────────────────────────────────
cd "${SCRIPT_DIR}"

# shellcheck disable=SC2086
pytest \
  -s -v \
  --tb=short \
  -p no:cacheprovider \
  ${HTML_ARGS} \
  test_lambda_aws_regression.py \
  ${EXTRA_ARGS}

EXIT_CODE=$?

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
if [ "${EXIT_CODE}" -eq 0 ]; then
  echo "  RESULT: ALL TESTS PASSED ✓"
else
  echo "  RESULT: ONE OR MORE TESTS FAILED (exit code ${EXIT_CODE})"
fi
echo "  JSON report : ${REPORT_DIR}/aws_regression_report.json"
echo "  HTML report : ${REPORT_DIR}/aws_regression_report.html"
if [ -n "${HTML_ARGS}" ]; then
  echo "  pytest HTML : ${REPORT_DIR}/aws_regression_report_pytest.html"
fi
echo "================================================================"
echo ""

exit "${EXIT_CODE}"
