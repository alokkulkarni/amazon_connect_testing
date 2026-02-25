#!/usr/bin/env bash
# =============================================================================
# run_tests.sh
# ============
# Local convenience wrapper to execute the Amazon Connect Dashboard test suite.
#
# Usage:
#   ./run_tests.sh [OPTIONS]
#
# Options (all optional – override defaults in config.json):
#   --suite  SUITE_ID      Run only the specified suite
#   --test   TEST_ID       Run only the specified test case
#   --dry-run              Print execution plan, do not call any APIs
#   --log-level LEVEL      DEBUG | INFO | WARNING | ERROR (default: INFO)
#   --concurrency N        Max concurrent tests (default: from config.json)
#   --output-dir DIR       Report output directory (default: from config.json)
#
# Environment variables (optional – override config.json values):
#   CONNECT_INSTANCE_ID    Amazon Connect instance UUID
#   CONNECT_INSTANCE_ALIAS Amazon Connect instance alias
#   AWS_REGION             AWS region (default: us-east-1)
#   AWS_ROLE_ARN           IAM role to assume (leave empty for local creds)
#   CONNECT_OUTPUT_DIR     Override output directory
#   CONNECT_CONCURRENCY    Override max concurrency
#
# Prerequisites:
#   • Python 3.11+ on PATH
#   • AWS credentials configured (env vars, ~/.aws/credentials, or OIDC role)
#
# Virtual environment:
#   A .venv is created automatically inside the script directory on first run.
#   Dependencies (boto3>=1.42.34, etc.) are installed into it – system Python
#   is never modified (avoids macOS PEP 668 / Homebrew restrictions).
#   Set VIRTUAL_ENV_DIR to override the venv location.
#
# Examples:
#   # Run all tests
#   ./run_tests.sh
#
#   # Run a single suite
#   ./run_tests.sh --suite SUITE-001
#
#   # Dry run with debug logging
#   ./run_tests.sh --dry-run --log-level DEBUG
# =============================================================================

set -euo pipefail

# ── Resolve the directory that contains this script ───────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Default configuration ─────────────────────────────────────────────────────
CONFIG_FILE="${SCRIPT_DIR}/config.json"
TEST_CASES_FILE="${SCRIPT_DIR}/test_cases.json"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
OUTPUT_DIR="${CONNECT_OUTPUT_DIR:-${SCRIPT_DIR}/test-results}"
VIRTUAL_ENV_DIR="${VIRTUAL_ENV_DIR:-${SCRIPT_DIR}/.venv}"
BOTO3_MIN_VERSION="1.42.34"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${RESET}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*"; }
log_step()  { echo -e "\n${BOLD}── $* ──${RESET}"; }

# ── Sanity checks ─────────────────────────────────────────────────────────────
log_step "Pre-flight checks"

if ! command -v "${PYTHON_BIN}" &>/dev/null; then
  log_error "Python binary '${PYTHON_BIN}' not found. Set PYTHON_BIN env var or ensure python3 is on PATH."
  exit 1
fi
PYTHON_VERSION="$("${PYTHON_BIN}" --version 2>&1)"
log_info "Python     : ${PYTHON_VERSION}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  log_error "config.json not found at: ${CONFIG_FILE}"
  exit 1
fi
log_info "Config     : ${CONFIG_FILE}"

if [[ ! -f "${TEST_CASES_FILE}" ]]; then
  log_error "test_cases.json not found at: ${TEST_CASES_FILE}"
  exit 1
fi
log_info "Test cases : ${TEST_CASES_FILE}"

# ── Virtual environment setup ────────────────────────────────────────────────
log_step "Setting up virtual environment"

REQ_FILE="${SCRIPT_DIR}/requirements.txt"

# Create venv if it does not exist yet
if [[ ! -f "${VIRTUAL_ENV_DIR}/bin/python" ]]; then
  log_info "Creating venv at: ${VIRTUAL_ENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VIRTUAL_ENV_DIR}"
  log_ok "Venv created."
else
  log_info "Venv       : ${VIRTUAL_ENV_DIR} (exists)"
fi

# Always use the venv's python / pip from here on
VENV_PYTHON="${VIRTUAL_ENV_DIR}/bin/python"
VENV_PIP="${VIRTUAL_ENV_DIR}/bin/pip"

# ── Dependency check ──────────────────────────────────────────────────────────
log_step "Checking Python dependencies"

if [[ -f "${REQ_FILE}" ]]; then
  # Check whether boto3 meets the minimum version requirement
  INSTALLED_BOTO3=$("${VENV_PYTHON}" -c "
import importlib.metadata as m
try:
    print(m.version('boto3'))
except m.PackageNotFoundError:
    print('0.0.0')
" 2>/dev/null)

  _ver_ge() {
    # Returns 0 (true) if \$1 >= \$2 in semver ordering
    python3 -c "from packaging.version import Version; exit(0 if Version('$1') >= Version('$2') else 1)" 2>/dev/null \
      || "${VENV_PYTHON}" -c "
import sys
a=[int(x) for x in '$1'.split('.')]; b=[int(x) for x in '$2'.split('.')]
sys.exit(0 if a >= b else 1)"
  }

  if ! _ver_ge "${INSTALLED_BOTO3}" "${BOTO3_MIN_VERSION}"; then
    log_warn "boto3 ${INSTALLED_BOTO3} < ${BOTO3_MIN_VERSION} (or not installed) – running pip install …"
    "${VENV_PIP}" install --quiet --upgrade -r "${REQ_FILE}"
    log_ok "Dependencies installed/upgraded."
  else
    log_ok "boto3 ${INSTALLED_BOTO3} >= ${BOTO3_MIN_VERSION} – dependencies satisfied."
  fi
else
  log_warn "requirements.txt not found – skipping dependency install."
fi

# ── Build the Python command ──────────────────────────────────────────────────
log_step "Building execution command"

RUNNER_SCRIPT="${SCRIPT_DIR}/run_connect_tests.py"
CMD=(
  "${VENV_PYTHON}" "${RUNNER_SCRIPT}"
  "--config"      "${CONFIG_FILE}"
  "--test-cases"  "${TEST_CASES_FILE}"
  "--output-dir"  "${OUTPUT_DIR}"
  "--log-level"   "${LOG_LEVEL}"
)

# Forward extra arguments (--suite, --test, --dry-run, --concurrency …)
CMD+=("$@")

log_info "Command: ${CMD[*]}"

# ── Create output directory ───────────────────────────────────────────────────
mkdir -p "${OUTPUT_DIR}"

# ── Execute ───────────────────────────────────────────────────────────────────
log_step "Executing tests"
echo ""

START_TIME=$(date +%s)
EXIT_CODE=0

"${CMD[@]}" || EXIT_CODE=$?

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))

echo ""
log_step "Execution complete"
log_info "Duration : ${ELAPSED}s"

# ── Report locations ──────────────────────────────────────────────────────────
# Reports are written into a timestamped subdirectory created by the runner;
# find the most-recently modified one and list its contents.
if [[ -d "${OUTPUT_DIR}" ]]; then
  # Most recently modified child directory (the just-created run folder)
  LATEST_RUN_DIR=$(find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -type d \
    | sort -r | head -n 1)
  if [[ -n "${LATEST_RUN_DIR}" ]]; then
    log_info "Reports written to: ${LATEST_RUN_DIR}"
    for f in "${LATEST_RUN_DIR}"/*.json "${LATEST_RUN_DIR}"/*.html "${LATEST_RUN_DIR}"/*.xml; do
      [[ -f "$f" ]] && log_info "  └── $(basename "$f")"
    done
  fi
fi

# ── Final status ──────────────────────────────────────────────────────────────
echo ""
if [[ "${EXIT_CODE}" -eq 0 ]]; then
  log_ok  "All tests passed."
else
  log_error "One or more tests failed (exit code: ${EXIT_CODE})."
fi

exit "${EXIT_CODE}"
