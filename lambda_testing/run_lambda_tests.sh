#!/usr/bin/env bash
# run_lambda_tests.sh – Run Lambda regression tests against LocalStack.
#
# Usage (from any directory):
#   ./lex_testing/run_lambda_tests.sh            # default – uses .env at repo root
#   PYTEST_ARGS="-k TC-001 -x" ./run_lambda_tests.sh  # filter / stop-on-first-failure
#
# Prerequisites:
#   - Docker running
#   - pip packages installed (pip install -r requirements.txt from repo root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Load .env: suite-local first, then repo root as fallback
# ---------------------------------------------------------------------------
if [ -f "${SCRIPT_DIR}/.env" ]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "${SCRIPT_DIR}/.env"
  set +o allexport
  echo "[info] Loaded environment from ${SCRIPT_DIR}/.env"
elif [ -f "${REPO_ROOT}/.env" ]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env"
  set +o allexport
  echo "[info] Loaded environment from ${REPO_ROOT}/.env (fallback)"
fi

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
  echo "[error] python3 not found. Install Python 3.9+"
  exit 1
fi

if ! command -v pytest &>/dev/null; then
  echo "[error] pytest not found. Run: pip install -r requirements.txt"
  exit 1
fi

if ! docker info &>/dev/null 2>&1; then
  echo "[error] Docker is not running. Start Docker Desktop and retry."
  exit 1
fi

# ---------------------------------------------------------------------------
# Container cleanup trap
# Removes the LocalStack container and any Lambda runtime sibling containers
# left on the host Docker daemon after the test run (or on early exit).
# LocalStack 3.x (new Lambda provider) spins up Docker containers on the host
# for each Lambda invocation; these survive LocalStack's own shutdown.
# ---------------------------------------------------------------------------
_cleanup_containers() {
  echo ""
  echo "[cleanup] Tearing down LocalStack and Lambda runtime containers ..."

  # Lambda runtime containers (both underscore and dash naming schemes)
  local lambda_ids
  lambda_ids=$(docker ps -aq \
    --filter "name=localstack_lambda" \
    --filter "name=localstack-lambda" 2>/dev/null || true)
  if [ -n "${lambda_ids}" ]; then
    # shellcheck disable=SC2086
    docker rm -f ${lambda_ids} 2>/dev/null || true
    echo "[cleanup] Lambda runtime containers removed."
  fi

  # Any remaining container whose name starts with 'localstack' (main container)
  local ls_ids
  ls_ids=$(docker ps -aq --filter "name=localstack" 2>/dev/null || true)
  if [ -n "${ls_ids}" ]; then
    # shellcheck disable=SC2086
    docker rm -f ${ls_ids} 2>/dev/null || true
    echo "[cleanup] LocalStack container removed."
  fi

  echo "[cleanup] Container teardown complete."
}

# Run cleanup on normal exit, Ctrl+C (SIGINT), and SIGTERM (CI kill)
trap _cleanup_containers EXIT INT TERM

# ---------------------------------------------------------------------------
# Create report directory
# ---------------------------------------------------------------------------
REPORT_DIR="${SCRIPT_DIR}/reports"
mkdir -p "${REPORT_DIR}"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo "  Lambda Regression Tests – LocalStack"
echo "================================================================"
echo "  Test cases : ${SCRIPT_DIR}/lambda_test_cases.json"
echo "  Lambda src  : ${SCRIPT_DIR}/sample_lambda.py"
echo "  Reports dir : ${REPORT_DIR}"
echo "================================================================"
echo ""

# ---------------------------------------------------------------------------
# Run pytest from SCRIPT_DIR so __file__-based paths resolve correctly.
# pytest-html produces the HTML report; conftest.py writes the JSON report.
# ---------------------------------------------------------------------------
EXTRA_ARGS="${PYTEST_ARGS:-}"

# Add --html flag only if pytest-html is installed
HTML_ARGS=""
if python3 -c "import pytest_html" 2>/dev/null; then
  HTML_ARGS="--html=${REPORT_DIR}/lambda_test_report.html --self-contained-html"
else
  echo "[warn] pytest-html not installed – skipping HTML report. Run: pip install pytest-html"
fi

cd "${SCRIPT_DIR}"
# shellcheck disable=SC2086
pytest \
  -s -v \
  --tb=short \
  ${HTML_ARGS} \
  test_lambda_localstack.py \
  ${EXTRA_ARGS}

EXIT_CODE=$?

echo ""
echo "================================================================"
if [ "${EXIT_CODE}" -eq 0 ]; then
  echo "  RESULT: ALL TESTS PASSED"
else
  echo "  RESULT: ONE OR MORE TESTS FAILED (exit code ${EXIT_CODE})"
fi
echo "  JSON report : ${REPORT_DIR}/lambda_test_report.json"
echo "  HTML report : ${REPORT_DIR}/lambda_test_report.html"
echo "================================================================"
echo ""

exit "${EXIT_CODE}"
