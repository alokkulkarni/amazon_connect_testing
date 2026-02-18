#!/usr/bin/env bash
# run_tests.sh – Run Amazon Connect voice flow regression tests.
#
# Usage (from any directory):
#   ./voice_testing/run_tests.sh                      # run all test cases
#   MOCK_AWS=true ./voice_testing/run_tests.sh        # dry-run without real AWS
#   PYTEST_ARGS="-k CF-E2E-001 -x" ./voice_testing/run_tests.sh  # filter
#
# Prerequisites:
#   - AWS credentials configured (or MOCK_AWS=true for dry-run)
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
echo "  Amazon Connect Voice Flow Tests"
echo "================================================================"
echo "  Test cases : ${SCRIPT_DIR}/test_cases.json"
echo "  Test file  : ${SCRIPT_DIR}/test_voice_flows.py"
echo "  Reports dir: ${REPORT_DIR}"
echo "  MOCK_AWS   : ${MOCK_AWS:-false}"
echo "================================================================"
echo ""

# ---------------------------------------------------------------------------
# Run pytest from SCRIPT_DIR so __file__-based paths resolve correctly.
# pytest-html produces the HTML report (optional).
# ---------------------------------------------------------------------------
EXTRA_ARGS="${PYTEST_ARGS:-}"

# Add --html flag only if pytest-html is installed
HTML_ARGS=""
if python3 -c "import pytest_html" 2>/dev/null; then
  HTML_ARGS="--html=${REPORT_DIR}/voice_test_report.html --self-contained-html"
else
  echo "[warn] pytest-html not installed – skipping HTML report. Run: pip install pytest-html"
fi

cd "${SCRIPT_DIR}"
# shellcheck disable=SC2086
pytest \
  -s -v \
  --tb=short \
  ${HTML_ARGS} \
  test_voice_flows.py \
  ${EXTRA_ARGS}

EXIT_CODE=$?

echo ""
echo "================================================================"
if [ "${EXIT_CODE}" -eq 0 ]; then
  echo "  RESULT: ALL TESTS PASSED"
else
  echo "  RESULT: ONE OR MORE TESTS FAILED (exit code ${EXIT_CODE})"
fi
if [ -n "${HTML_ARGS}" ]; then
  echo "  HTML report: ${REPORT_DIR}/voice_test_report.html"
fi
echo "================================================================"
echo ""

exit "${EXIT_CODE}"
