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
#   • AWS credentials via ONE approved mechanism – see credential policy below.
#       ⚠ NEVER set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY directly;
#         the script detects and rejects static key pairs at startup.
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

# ── AWS role-assumption variables ─────────────────────────────────────────────
#
# ⚠  CREDENTIAL POLICY – enforced at runtime
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN must NEVER
#   be set manually. The script exits 1 if it detects them in the environment.
#
# Approved credential mechanisms (evaluated in this order):
#   1. GitHub Actions OIDC  – aws-actions/configure-aws-credentials@v4 injects
#                             short-lived STS creds; detected via GITHUB_ACTIONS=true.
#   2. AWS_ROLE_ARN         – script calls sts:AssumeRole on your behalf using
#                             base credentials from ~/.aws/config or AWS_PROFILE.
#   3. Local AWS config     – ~/.aws/credentials or ~/.aws/config
#                             (run 'aws configure' or 'aws configure sso').
#
# To use a specific IAM role, set AWS_ROLE_ARN (and optionally the
# variables below) before calling this script, or export them in your shell:
#
#   export AWS_ROLE_ARN="arn:aws:iam::123456789012:role/ConnectTestRunner"
#   ./run_tests.sh
#
# ─── VARIABLE REFERENCE ───────────────────────────────────────────────────────
#
#  AWS_ROLE_ARN              Full ARN of the role to assume.
#                            Example: arn:aws:iam::123456789012:role/ConnectTestRunner
#                            Leave empty (default) to skip role assumption and use
#                            ambient credentials.
#
#  AWS_ROLE_SESSION_NAME     Label shown in CloudTrail for this session.
#                            Default: connect-test-runner
#
#  AWS_ROLE_EXTERNAL_ID      External ID required by the role's trust policy.
#                            Only needed when the trust policy has an ExternalId
#                            condition (common for cross-account roles).
#                            Default: (empty – omitted from the STS call)
#
#  AWS_ROLE_SESSION_DURATION Session token lifetime in seconds (900–43200).
#                            Must not exceed the role's MaxSessionDuration.
#                            Default: 3600 (1 hour)
#
# ─── HOW TO CREATE THE ROLE, POLICY AND PERMISSIONS ──────────────────────────
#
# The full permission policy JSON is in: ./iam_permission_policy.json
# The trust policy template is in:       ./iam_trust_policy.json
#
# Step 1 – Create the permission policy
#   a) AWS IAM Console → Policies → Create policy → JSON tab.
#   b) Paste the content of iam_permission_policy.json.
#      Replace REGION / ACCOUNT_ID / CONNECT_INSTANCE_ID with real values.
#   c) Name it e.g. "ConnectTestRunnerPolicy" and click Create policy.
#
# Step 2 – Create the IAM role
#   a) IAM Console → Roles → Create role.
#   b) Trusted entity type: "AWS account" (same-account or cross-account assume-role).
#      For CI/CD via GitHub Actions OIDC use "Web identity" (see iam_trust_policy.json).
#   c) For local assume-role the trust policy must allow your calling identity:
#      {
#        "Version": "2012-10-17",
#        "Statement": [{
#          "Effect": "Allow",
#          "Principal": { "AWS": "arn:aws:iam::ACCOUNT_ID:user/YOUR_USERNAME" },
#          "Action": "sts:AssumeRole"
#          // Optional: add ExternalId condition for extra security:
#          // "Condition": { "StringEquals": { "sts:ExternalId": "your-external-id" } }
#        }]
#      }
#   d) Attach the "ConnectTestRunnerPolicy" created in Step 1.
#   e) Name the role e.g. "ConnectTestRunner", set MaxSessionDuration (min 3600),
#      and note the Role ARN.
#
# Step 3 – Grant your local user permission to call sts:AssumeRole
#   Add an inline or managed policy to your IAM user/profile:
#   {
#     "Version": "2012-10-17",
#     "Statement": [{
#       "Effect": "Allow",
#       "Action": "sts:AssumeRole",
#       "Resource": "arn:aws:iam::ACCOUNT_ID:role/ConnectTestRunner"
#     }]
#   }
#
# Step 4 – Use the role locally
#   export AWS_ROLE_ARN="arn:aws:iam::123456789012:role/ConnectTestRunner"
#   # (optional) export AWS_ROLE_EXTERNAL_ID="your-secret-external-id"
#   ./run_tests.sh
#
#   Or add to .env (sourced automatically if present at script startup):
#     AWS_ROLE_ARN=arn:aws:iam::123456789012:role/ConnectTestRunner
#
# Step 5 – CI/CD (GitHub Actions OIDC)
#   Use the OIDC trust policy in iam_trust_policy.json rather than long-lived keys.
#   Store the role ARN as the repository secret AWS_ROLE_ARN.
#   The workflow calls aws-actions/configure-aws-credentials before this script;
#   temporary credentials are already in the environment so assume-role is skipped.
#
# ─── REQUIRED PERMISSIONS SUMMARY ────────────────────────────────────────────
#   connect:StartTestCaseExecution         – start a test execution
#   connect:GetTestCaseExecutionSummary    – poll execution status
#   connect:ListTestCaseExecutionRecords   – retrieve per-step records
#   connect:DescribeTestCase               – look up test case metadata
#   connect:ListInstances / DescribeInstance, ListContactFlows, etc. – read-only lookups
#   sts:GetCallerIdentity                  – log the assumed identity (optional but useful)
#   sts:AssumeRole (on the CALLING identity) – required only when AWS_ROLE_ARN is set
# ──────────────────────────────────────────────────────────────────────────────
ASSUME_ROLE_ARN="${AWS_ROLE_ARN:-}"
ASSUME_ROLE_SESSION_NAME="${AWS_ROLE_SESSION_NAME:-connect-test-runner}"
ASSUME_ROLE_EXTERNAL_ID="${AWS_ROLE_EXTERNAL_ID:-}"
ASSUME_ROLE_SESSION_DURATION="${AWS_ROLE_SESSION_DURATION:-3600}"

# Tracks whether this script assumed a role (used for cleanup at the end)
_ASSUMED_ROLE=false

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

# ── AWS credential resolution ─────────────────────────────────────────────────
log_step "Resolving AWS credentials"

# Load .env from the script directory if it exists and has not already been
# sourced (useful for setting AWS_ROLE_ARN, CONNECT_INSTANCE_ID, etc. locally
# without polluting the shell profile).
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  log_info "Sourcing ${SCRIPT_DIR}/.env"
  # shellcheck disable=SC1090
  set -a; source "${SCRIPT_DIR}/.env"; set +a
  # Re-read assume-role variables in case .env set them after the defaults block
  ASSUME_ROLE_ARN="${AWS_ROLE_ARN:-${ASSUME_ROLE_ARN}}"
  ASSUME_ROLE_SESSION_NAME="${AWS_ROLE_SESSION_NAME:-${ASSUME_ROLE_SESSION_NAME}}"
  ASSUME_ROLE_EXTERNAL_ID="${AWS_ROLE_EXTERNAL_ID:-${ASSUME_ROLE_EXTERNAL_ID}}"
  ASSUME_ROLE_SESSION_DURATION="${AWS_ROLE_SESSION_DURATION:-${ASSUME_ROLE_SESSION_DURATION}}"
fi

# ── Static-credential guard ───────────────────────────────────────────────────
# Called immediately after .env is sourced so any keys loaded from .env are
# also caught before any AWS API call is made.
#
# POLICY SUMMARY
#   Static long-lived key pair  (key+secret, NO token)   → always rejected
#   Pre-set STS triplet         (key+secret+token) local → rejected; use
#                                AWS_ROLE_ARN and let this script manage the session
#   OIDC-injected STS triplet   (key+secret+token) in    → accepted
#                                GitHub Actions (GITHUB_ACTIONS=true only)
_guard_preexisting_creds() {
  local key_set=false secret_set=false token_set=false
  [[ -n "${AWS_ACCESS_KEY_ID:-}"     ]] && key_set=true
  [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && secret_set=true
  [[ -n "${AWS_SESSION_TOKEN:-}"     ]] && token_set=true

  if [[ "${key_set}" == "true" || "${secret_set}" == "true" ]]; then

    if [[ "${token_set}" == "false" ]]; then
      # ── Long-lived static key pair – always rejected ────────────────────
      log_error "═══════════════════════════════════════════════════════════"
      log_error "  SECURITY VIOLATION: Static AWS key pair detected"
      log_error "═══════════════════════════════════════════════════════════"
      log_error "  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are set without"
      log_error "  AWS_SESSION_TOKEN. Long-lived static credentials are NOT"
      log_error "  permitted by this project's security policy."
      log_error ""
      log_error "  Approved credential sources:"
      log_error "  ┌─ Local ─────────────────────────────────────────────────"
      log_error "  │  ~/.aws/credentials  → run: aws configure"
      log_error "  │  ~/.aws/config + SSO → run: aws configure sso"
      log_error "  │  export AWS_PROFILE=<profile>    (select named profile)"
      log_error "  │  export AWS_ROLE_ARN=<role-arn>  (script assumes role)"
      log_error "  ├─ CI/CD (GitHub Actions) ───────────────────────────────"
      log_error "  │  OIDC only via aws-actions/configure-aws-credentials@v4"
      log_error "  │    role-to-assume: \${{ secrets.AWS_ROLE_ARN }}"
      log_error "  │  Never store key pairs as repository secrets."
      log_error "  └────────────────────────────────────────────────────────"
      log_error ""
      log_error "  Fix: unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY"
      log_error "═══════════════════════════════════════════════════════════"
      exit 1
    fi

    # All three STS vars are set
    if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
      # ── Pre-set STS triplet locally – rejected; use AWS_ROLE_ARN instead ─
      log_error "═══════════════════════════════════════════════════════════"
      log_error "  SECURITY VIOLATION: Pre-set STS credentials detected"
      log_error "═══════════════════════════════════════════════════════════"
      log_error "  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and"
      log_error "  AWS_SESSION_TOKEN are all present in the environment."
      log_error "  Manually injecting STS sessions locally is NOT permitted."
      log_error ""
      log_error "  Use AWS_ROLE_ARN instead – this script calls sts:AssumeRole"
      log_error "  using your ~/.aws config and manages the session for you:"
      log_error "    export AWS_ROLE_ARN=arn:aws:iam::ACCOUNT_ID:role/ROLE_NAME"
      log_error ""
      log_error "  Fix: unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN"
      log_error "═══════════════════════════════════════════════════════════"
      exit 1
    fi

    # GITHUB_ACTIONS=true + all three STS vars → OIDC-managed; approved
    log_ok  "GitHub Actions OIDC credentials detected (key+secret+token all present)."
    log_info "Injected by aws-actions/configure-aws-credentials – approved mechanism."
    log_info "Skipping sts:AssumeRole (session is already active)."
    _OIDC_CREDS_ACTIVE=true
  fi
}

_OIDC_CREDS_ACTIVE=false
_guard_preexisting_creds

if [[ "${_OIDC_CREDS_ACTIVE}" == "true" ]]; then
  # ── GitHub Actions OIDC path ──────────────────────────────────────────────
  # Short-lived STS credentials injected upstream by OIDC action; nothing to do
  # except verify and log the assumed identity for the audit trail.
  _CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo '(verification failed)')"
  log_info "Caller  : ${_CALLER_ARN}"

elif [[ -n "${ASSUME_ROLE_ARN}" ]]; then
  # ── Assume-role path ───────────────────────────────────────────────────────
  log_info "AWS_ROLE_ARN is set – will assume role via sts:AssumeRole"
  log_info "Role    : ${ASSUME_ROLE_ARN}"
  log_info "Session : ${ASSUME_ROLE_SESSION_NAME}  (duration: ${ASSUME_ROLE_SESSION_DURATION}s)"

  # The aws CLI is required for assume-role; boto3 alone cannot be used here
  # because the venv has not been created/activated yet at this point.
  if ! command -v aws &>/dev/null; then
    log_error "aws CLI not found but AWS_ROLE_ARN is set."
    log_error "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
    exit 1
  fi

  # Build the sts assume-role command array
  STS_CMD=(
    aws sts assume-role
    --role-arn              "${ASSUME_ROLE_ARN}"
    --role-session-name     "${ASSUME_ROLE_SESSION_NAME}"
    --duration-seconds      "${ASSUME_ROLE_SESSION_DURATION}"
    --output json
  )
  # Append --external-id only when configured (avoids sending an empty value)
  if [[ -n "${ASSUME_ROLE_EXTERNAL_ID}" ]]; then
    STS_CMD+=(--external-id "${ASSUME_ROLE_EXTERNAL_ID}")
    log_info "ExternalId: ${ASSUME_ROLE_EXTERNAL_ID}"
  fi

  log_info "Calling : ${STS_CMD[*]}"

  # Execute assume-role and capture the full JSON response
  STS_RESPONSE="$( "${STS_CMD[@]}" 2>&1 )" || {
    log_error "sts:AssumeRole failed. Raw output:"
    echo "${STS_RESPONSE}"
    echo ""
    log_error "Common causes:"
    log_error "  1) Your local credentials do not have sts:AssumeRole on ${ASSUME_ROLE_ARN}"
    log_error "     → Add an inline policy to your user/role granting sts:AssumeRole on that ARN"
    log_error "  2) The role trust policy does not allow your caller identity as a Principal"
    log_error "     → Edit the role trust relationship in IAM Console (see comments at top of script)"
    log_error "  3) AWS_ROLE_EXTERNAL_ID is missing or wrong"
    log_error "     → Check the trust policy's StringEquals sts:ExternalId condition"
    log_error "  4) Your local clock is skewed (STS is sensitive to time drift)"
    log_error "     → Sync system clock: sudo sntp -sS time.apple.com"
    exit 1
  }

  # Extract the three temporary credential fields from the JSON response.
  # python3 (system or venv) is used to avoid a jq dependency.
  _sts_field() { echo "${STS_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['Credentials']['$1'])"; }
  export AWS_ACCESS_KEY_ID;     AWS_ACCESS_KEY_ID="$(    _sts_field AccessKeyId)"
  export AWS_SECRET_ACCESS_KEY; AWS_SECRET_ACCESS_KEY="$(_sts_field SecretAccessKey)"
  export AWS_SESSION_TOKEN;     AWS_SESSION_TOKEN="$(    _sts_field SessionToken)"
  _STS_EXPIRY="$(_sts_field Expiration 2>/dev/null || echo 'unknown')"

  # Unset any profile that might override the exported credentials
  unset AWS_PROFILE

  _ASSUMED_ROLE=true
  log_ok  "Role assumed successfully."
  log_info "Expires : ${_STS_EXPIRY}"

  # Verify and log the assumed identity – useful for audit trails in CI logs
  _CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo '(verification failed)')"
  log_info "Caller  : ${_CALLER_ARN}"

else
  # ── Local AWS config path ─────────────────────────────────────────────────
  # No pre-set credentials, no OIDC, no AWS_ROLE_ARN → boto3 resolves from the
  # standard local credential chain: ~/.aws/credentials, ~/.aws/config, or an
  # EC2/ECS/Lambda instance-metadata endpoint (inside cloud environments).
  log_info "No role ARN configured – resolving credentials from local AWS config."
  log_info "Source   : ~/.aws/credentials or ~/.aws/config"
  log_info "Profile  : ${AWS_PROFILE:-(default)}"
  log_info "Tip: export AWS_PROFILE=<profile>   to use a named profile"
  log_info "Tip: export AWS_ROLE_ARN=<role-arn> to have this script assume a role"

  # If aws CLI is available, log the current identity for visibility.
  # A failure here is non-fatal – boto3 may still find credentials.
  if command -v aws &>/dev/null; then
    _CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo '(unable to determine – credentials may not be configured)')"
    log_info "Caller  : ${_CALLER_ARN}"
  else
    log_warn "aws CLI not found – skipping identity verification. boto3 will resolve credentials at runtime."
  fi
fi

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
# ── Clean up temporary assumed-role credentials ───────────────────────────────
# Unset the short-lived STS credentials so they cannot be inadvertently reused
# by any process started after this script exits.
if [[ "${_ASSUMED_ROLE}" == "true" ]]; then
  log_info "Unsetting temporary STS credentials (assumed-role session ending)."
  unset AWS_ACCESS_KEY_ID
  unset AWS_SECRET_ACCESS_KEY
  unset AWS_SESSION_TOKEN
fi

if [[ "${EXIT_CODE}" -eq 0 ]]; then
  log_ok  "All tests passed."
else
  log_error "One or more tests failed (exit code: ${EXIT_CODE})."
fi

exit "${EXIT_CODE}"
