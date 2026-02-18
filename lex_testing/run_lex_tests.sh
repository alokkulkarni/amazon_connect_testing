#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_lex_tests.sh
# Runs the Amazon Lex V2 regression test suite.
#
# Usage (from repo root):
#   ./lex_testing/run_lex_tests.sh
#
# Usage (from inside lex_testing/):
#   ./run_lex_tests.sh
#
# Required env vars (or set in .env at repo root, or per test case in lex_test_cases.json):
#   LEX_BOT_ID       – Lex V2 Bot ID
#   LEX_BOT_ALIAS_ID – Lex V2 Bot Alias ID (e.g. TSTALIASID for Test Alias)
#   LEX_LOCALE_ID    – Locale (default: en_US)
#   AWS_REGION       – AWS region (default: us-east-1)
#   CONNECT_INSTANCE_ID – only needed if running combined test suites
# ---------------------------------------------------------------------------

set -euo pipefail

# ---- Resolve script location so this works from any cwd ------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---- Load .env if present at repo root -----------------------------------
if [ -f "${REPO_ROOT}/.env" ]; then
    # shellcheck source=/dev/null
    set -a; source "${REPO_ROOT}/.env"; set +a
    echo "Loaded .env from ${REPO_ROOT}/.env"
fi

# ---- Banner --------------------------------------------------------------
echo "================================================================"
echo "  AMAZON LEX V2 – REGRESSION TEST SUITE"
echo "  Bot ID   : ${LEX_BOT_ID:-<not set – configure in .env or test cases>}"
echo "  Alias ID : ${LEX_BOT_ALIAS_ID:-<not set – configure in .env or test cases>}"
echo "  Locale   : ${LEX_LOCALE_ID:-en_US}"
echo "  Region   : ${AWS_REGION:-us-east-1}"
echo "  Test file: ${SCRIPT_DIR}/lex_test_cases.json"
echo "================================================================"

# ---- Pre-flight checks ---------------------------------------------------
if ! command -v pytest &>/dev/null; then
    echo "ERROR: pytest is not installed."
    echo "  Run:  pip install -r ${REPO_ROOT}/requirements.txt"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not on PATH."
    exit 1
fi

# Warn (not fail) if credentials look absent – tests will skip rather than error
if [ -z "${AWS_ACCESS_KEY_ID:-}" ] && [ ! -f "${HOME}/.aws/credentials" ] && [ ! -f "${HOME}/.aws/config" ]; then
    echo "WARNING: No AWS credentials detected. Tests requiring a live bot will skip."
fi

# ---- Determine pytest arguments ------------------------------------------
# -s   : allow print() output to reach the terminal
# -v   : verbose (one line per test)
# --tb=short : compact tracebacks
# Optionally override with PYTEST_ARGS env var, e.g.
#   PYTEST_ARGS="-k INTENT -x" ./lex_testing/run_lex_tests.sh
PYTEST_ARGS="${PYTEST_ARGS:--s -v --tb=short}"

# ---- Run tests -----------------------------------------------------------
echo ""
echo "Running: pytest ${PYTEST_ARGS} ${SCRIPT_DIR}/test_lex_bots.py"
echo ""

# Change to repo root so relative imports (e.g. .env discovery) work
cd "${REPO_ROOT}"

# shellcheck disable=SC2086
pytest ${PYTEST_ARGS} "${SCRIPT_DIR}/test_lex_bots.py"
EXIT_CODE=$?

# ---- Summary -------------------------------------------------------------
echo ""
echo "================================================================"
if [ "${EXIT_CODE}" -eq 0 ]; then
    echo "  ALL LEX TESTS PASSED"
else
    echo "  SOME LEX TESTS FAILED (exit code: ${EXIT_CODE})"
fi
echo "================================================================"

exit "${EXIT_CODE}"
