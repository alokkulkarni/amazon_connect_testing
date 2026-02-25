# Amazon Connect Voice Simulation Tests (Dashboard-based)

This folder contains everything needed to **execute test cases created in the Amazon Connect Test Desk (dashboard)** through a CI/CD pipeline, locally, or on demand.

---

## Overview

Tests are **created once in the Amazon Connect console** (Connect → Test Desk → Test cases) and then driven programmatically via the following Connect APIs:

| API | Purpose |
|-----|---------|
| `StartTestCaseExecution` | Kick off a single test case run |
| `GetTestCaseExecutionSummary` | Poll for status + observation summary (`INITIATED` / `IN_PROGRESS` / `PASSED` / `FAILED` / `STOPPED`) |
| `ListTestCaseExecutionRecords` | Retrieve per-observation execution records on completion |

### Batched-concurrent execution

Tests run in **sequential batches** of up to `CONNECT_CONCURRENCY` (default **5**) concurrent executions per batch. After every batch completes, a dedicated set of reports is written immediately. The next batch then starts. When all batches finish, a final aggregated report is produced.

```
selected = 13 tests,  CONNECT_CONCURRENCY = 5

  Batch 01 ── 5 concurrent ──▶ batch report written  (connect_test_results_batch_01.*)
  Batch 02 ── 5 concurrent ──▶ batch report written  (connect_test_results_batch_02.*)
  Batch 03 ── 3 concurrent ──▶ batch report written  (connect_test_results_batch_03.*)
   └─ all done ──▶ final combined report             (connect_test_results.*)
```

This means you get partial results early and every intermediate batch is available as a GitHub Actions artefact.

---

## Folder structure

```
connect_voice_simulated/
├── .env                       # ← NOT committed – your local secrets (copy from .env.example)
├── .env.example               # Template – copy to .env and fill in your values
├── config.json                # All runtime parameters (ENV-VAR tokens resolved at startup)
├── test_cases.json            # Test case registry (suite ↔ Connect test case ID mapping)
├── run_connect_tests.py       # Main test runner (batched concurrent + report delegation)
├── report_generator.py        # HTML and XML report writers
├── requirements.txt           # Python dependencies
├── run_tests.sh               # Local convenience wrapper
├── iam_trust_policy.json      # GitHub OIDC trust relationship for the IAM role
├── iam_permission_policy.json # Least-privilege IAM permission policy
└── test-results/              # Generated at runtime – not committed to git
    ├── connect_test_results.json          ← final combined report
    ├── connect_test_results.html          ← final combined report (HTML)
    ├── connect_test_results.xml           ← JUnit – final combined report
    ├── connect_test_results_batch_01.*    ← per-batch intermediate reports
    ├── connect_test_results_batch_02.*
    └── test_runner.log
```

```
.github/workflows/
└── connect-voice-tests.yml    # GitHub Actions on-demand (workflow_dispatch) pipeline
```

---

## Quick-start (local)

### 1. Prerequisites

- Python 3.11+
- AWS credentials configured (`~/.aws/credentials`, env vars, or SSO) **for region `eu-west-2`**
- Amazon Connect instance with Test Desk tests already created

### 2. Install dependencies

```bash
cd connect_voice_simulated
pip install -r requirements.txt
```

### 3. Configure your `.env`

```bash
cp .env.example .env
# Then edit .env with your real values (see table below)
```

> `.env` is listed in `.gitignore` and **must never be committed**.

#### `.env` variable reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AWS_REGION` | ✅ | `eu-west-2` | AWS region of your Connect instance |
| `CONNECT_INSTANCE_ID` | ✅ | – | Connect instance UUID (from console URL) |
| `CONNECT_INSTANCE_ALIAS` | ✗ | – | Friendly alias shown in the Connect URL |
| `AWS_ROLE_ARN` | CI only | – | IAM role ARN to assume (leave blank for local dev) |
| `AWS_ROLE_SESSION_NAME` | ✗ | `connect-voice-test-runner` | STS session name (CloudTrail label) |
| `CONNECT_OUTPUT_DIR` | ✗ | `test-results` | Directory for JSON/HTML/XML reports + log |
| `CONNECT_CONCURRENCY` | ✗ | `5` | Tests per batch (max concurrent per batch) |
| `CONNECT_PHONE_NUMBER` | ✗ | – | Claimed DID in `+country-number` format |
| `CONNECT_MAIN_FLOW_ID` | ✗ | – | Main IVR contact flow ID |
| `CONNECT_LOAN_FLOW_ID` | ✗ | – | Loan-enquiry contact flow ID |
| `CONNECT_DISPUTE_FLOW_ID` | ✗ | – | Dispute-resolution contact flow ID |
| `CONNECT_GENERAL_QUEUE_ID` | ✗ | – | General-banking queue ID |
| `CONNECT_GOLD_QUEUE_ID` | ✗ | – | Gold/priority queue ID |

Variable resolution priority (highest → lowest):

```
OS / shell environment  >  .env file  >  config.json default  >  compiled default
```

### 4. Register your test case IDs

Edit `test_cases.json` and replace every `REPLACE_WITH_CONNECT_TEST_CASE_ID_*` placeholder with the real Connect test case UUID. You find it in the Connect console:

```
Connect console → Test Desk → Test cases → click a test → copy ID from URL or detail pane
```

### 5. Run

```bash
./run_tests.sh                            # all tests (batched, 5 concurrent per batch)
./run_tests.sh --suite SUITE-001          # specific suite only
./run_tests.sh --test <test_case_id>      # single test
./run_tests.sh --dry-run                  # plan only – no API calls
./run_tests.sh --log-level DEBUG          # verbose output
./run_tests.sh --concurrency 3            # override batch size to 3
```

Or invoke Python directly:

```bash
python run_connect_tests.py \
  --config     config.json \
  --test-cases test_cases.json \
  --output-dir test-results \
  --concurrency 5 \
  --log-level   INFO
```

---

## GitHub Actions – deploy and run the pipeline

### Step 1 – Add the GitHub OIDC provider to your AWS account

No long-lived AWS keys are stored in GitHub. The workflow uses **GitHub's OIDC provider** to assume an IAM role.

1. In the AWS console, go to **IAM → Identity Providers → Add provider**.
2. Select **OpenID Connect** and enter:
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
3. Click **Add provider** (this is a one-time account-level operation).

### Step 2 – Create the IAM role

1. In **IAM → Roles → Create role**, choose **Web identity**.
2. Select the identity provider you just created (`token.actions.githubusercontent.com`).
3. Audience: `sts.amazonaws.com`.
4. Click **Next** and open `iam_trust_policy.json` in this folder – substitute:
   - `ACCOUNT_ID` → your 12-digit AWS account ID
   - `GITHUB_ORG` → your GitHub organisation or user name
   - `GITHUB_REPO` → your repository name
5. Paste the edited JSON into the trust policy editor.
6. Attach the permission policy from `iam_permission_policy.json` (use **Create inline policy** or a managed policy).
   - The policy grants only what is needed: `connect:StartTestCaseExecution`, `connect:GetTestCaseExecutionSummary`, `connect:ListTestCaseExecutionRecords`, `connect:DescribeTestCase`, and `sts:GetCallerIdentity`.
7. Name the role (e.g. `ConnectTestRunnerRole`) and save.
8. Copy the **Role ARN** – you will need it in the next step.

### Step 3 – Configure GitHub repository secrets and variables

Go to your repository → **Settings → Secrets and variables → Actions**.

#### Secrets

| Name | Value |
|------|-------|
| `AWS_ROLE_ARN` | IAM role ARN from Step 2 |

#### Variables (Repository variables)

| Name | Value |
|------|-------|
| `CONNECT_INSTANCE_ID` | Amazon Connect instance UUID |
| `AWS_REGION` | `eu-west-2` (or your region) |
| `CONNECT_INSTANCE_ALIAS` | (optional) instance alias |

### Step 4 – Trigger the workflow

1. Go to **Actions** tab in your repository.
2. Select **Amazon Connect – Voice Simulation Tests** from the left sidebar.
3. Click **Run workflow** (top-right).
4. Fill in the optional inputs and click **Run workflow**.

#### Workflow inputs

| Input | Default | Description |
|-------|---------|-------------|
| `suite_id` | *(all)* | Run only a specific suite, e.g. `SUITE-001`. Leave blank for all. |
| `test_case_id` | *(all)* | Run only a single test case ID. Leave blank for all. |
| `concurrency` | `5` | Tests per batch (max concurrent within each batch) |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `dry_run` | `false` | Print plan without calling Connect APIs |

### Step 5 – What happens during the run

```
1. Checkout repository
2. Set up Python 3.11
3. Install dependencies (pip install -r requirements.txt)
4. Assume IAM role via GitHub OIDC (no long-lived credentials)
5. Verify AWS caller identity + Connect access
6. Build CLI argument string from workflow inputs
7. Run connect_voice_simulated/run_connect_tests.py
     └─ Batch 01: tests 1-5 concurrent → batch_01 report written
     └─ Batch 02: tests 6-10 concurrent → batch_02 report written
     └─ ...every batch produces its own JSON + HTML + XML report...
     └─ Final: aggregate all batches → combined report written
8. Parse JSON results → extract statistics
9. Cat full HTML report to step log (visible immediately)
10. Write GitHub Actions job summary (pass/fail table + suite breakdown)
11. Upload artefacts:
       connect_test_results.json / .html / .xml  ← combined final
       connect_test_results_batch_01.*            ← per-batch reports
       connect_test_results_batch_02.*
       test_runner.log
12. Publish JUnit XML annotations (individual test failures shown as check annotations)
13. Fail the job if any test failed (exit 1)
```

### Step 6 – Reading the results

| Where | What you see |
|-------|-------------|
| **Actions run → job summary** | Pass-rate, suite breakdown table, per-test status table |
| **Actions run → step log (step 9)** | Full styled HTML report output inline |
| **Actions run → Artefacts** | `connect-test-results-{run_number}.zip` containing all report files (final + per-batch), retained 30 days |
| **Commit check annotations** | Individual test failures annotated directly on the commit |

---

## How many test cases can a suite contain?

There is **no hard AWS limit** on the number of test cases per suite in the Test Desk. The batching mechanism in this runner handles any number transparently.

### Practical guidance

| Factor | Value | Impact |
|--------|-------|--------|
| **Batch size (concurrency)** | 5 (default) | Tests within each batch run simultaneously |
| **Poll interval** | 5 seconds | Time between `GetTestCaseExecutionSummary` calls |
| **Max poll attempts** | 60 | 60 × 5 s = **5-minute timeout per test** |
| **GitHub Actions job limit** | 6 hours | Upper bound on total pipeline runtime |
| **Recommended suite size** | ≤ 50 test cases | 10 batches × ≤ 5 min = ≤ 50 min per run |
| **Maximum practical suite size** | ~100 test cases | 20 batches × ≤ 5 min = ≤ 100 min |

> **Rule of thumb:** each additional 5 tests adds up to 5 minutes of pipeline time. Keep suites to **50 tests or fewer** for comfortable CI runs. Larger suites (up to ~100) are feasible but approach the 2-hour mark.

### Scaling beyond a single suite

If you need more tests, split them across **multiple suites** and trigger separate workflow runs per suite:

```
SUITE-001   ← core regression (25 tests)
SUITE-002   ← edge-case / boundary (25 tests)
SUITE-003   ← load / spike scenario (10 tests)
```

Each suite run is independent and its artefacts are uploaded separately.

---

## Configuration reference (`config.json`)

| Path | `.env` / env-var override | Description |
|------|--------------------------|-------------|
| `connect.instance_id` | `CONNECT_INSTANCE_ID` | Connect instance UUID |
| `connect.region` | `AWS_REGION` | AWS region (default: `eu-west-2`) |
| `iam.role_arn` | `AWS_ROLE_ARN` | Role to assume (blank = use ambient creds) |
| `iam.session_name` | `AWS_ROLE_SESSION_NAME` | STS session name |
| `execution.max_concurrent_tests` | `CONNECT_CONCURRENCY` | Batch size (concurrent tests per batch) |
| `execution.poll_interval_seconds` | – | Seconds between status polls |
| `execution.max_poll_attempts` | – | Max polls before marking STOPPED |
| `execution.retry_attempts` | – | Retries on transient API failures |
| `reporting.output_dir` | `CONNECT_OUTPUT_DIR` | Report output directory |
| `reporting.include_transcripts` | – | Include per-step transcripts in HTML/XML |

---

## Adding a new test case

1. **Create the test** in the Amazon Connect console (Test Desk → Test cases → Create test case).
2. Copy the test case UUID from the console URL or the detail pane.
3. Add an entry to the appropriate suite in `test_cases.json`:

```json
{
  "test_case_id": "YOUR_CONNECT_TEST_CASE_UUID",
  "name": "CVS-NNN – Short descriptive name",
  "description": "What this test validates.",
  "contact_flow_name": "YourContactFlowName",
  "expected_queue": "YourQueueName",
  "expected_outcome": "QUEUED",
  "tags": ["regression"],
  "timeout_seconds": 120
}
```

4. Commit and push. The test is picked up automatically on the next workflow run.

---

## Report formats

### JSON (`connect_test_results.json`)

Machine-readable. Contains run metadata (`run_id`, `started_at`, `finished_at`, `statistics`) plus a `results` array with per-test details including observation records and transcripts.

### HTML (`connect_test_results.html`)

- Single self-contained file (no external dependencies)
- Executive summary with pass-rate progress bar
- Per-suite breakdown table
- Collapsible step-detail tables per test case
- Chat-style transcript tables per step
- Colour-coded status badges

### XML (`connect_test_results.xml`)

JUnit-compatible. Each suite → `<testsuite>`, each test → `<testcase>`. Step details and transcripts are embedded in `<system-out>` CDATA blocks.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Missing required config value 'connect.instance_id'` | `CONNECT_INSTANCE_ID` not set | Add it to `.env` or GitHub Variables |
| `botocore.exceptions.NoCredentialsError` | No AWS credentials | Configure `~/.aws/credentials` or set `CONNECT_CONCURRENCY_ID` env var |
| `AccessDeniedException` on `StartTestCaseExecution` | IAM policy missing `connect:StartTestCaseExecution` | Attach `iam_permission_policy.json` to the role |
| Tests stuck in `IN_PROGRESS` until timeout | Poll limit reached | Increase `execution.max_poll_attempts` in `config.json` or set a higher `timeout_seconds` per test |
| No results file produced | Python error before runner completes | Check `test_runner.log` in artefacts for the stack trace |
