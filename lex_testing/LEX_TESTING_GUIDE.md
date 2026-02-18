# Amazon Lex V2 Bot Regression Testing

This directory contains a self-contained regression test suite for Amazon Lex V2 bots.  
Tests validate **intent recognition**, **slot filling**, **dialog state transitions**, **session attributes**, **active contexts**, **intent redirections**, and **response messages** for every conversation path in your bot.

---

## Directory Structure

```
lex_testing/
├── test_lex_bots.py       # pytest test runner (single-turn and multi-turn)
├── lex_test_cases.json    # all test cases (intents, slots, turns, sessions…)
├── run_lex_tests.sh       # shell runner (use from any working directory)
└── LEX_TESTING_GUIDE.md   # this file
```

---

## Setup

### 1. Prerequisites

- Python 3.9+
- AWS credentials configured (`~/.aws/credentials`, AWS SSO, or environment variables)
- A deployed Lex V2 bot with a **Bot ID** and a **Bot Alias ID**

### 2. Install dependencies

From the repo root:

```bash
pip install -r requirements.txt
```

Or install only what the Lex tests need:

```bash
pip install boto3 botocore pytest python-dotenv
```

### 3. Configuration

Set credentials and bot identifiers via environment variables or a `.env` file at the repo root.

**Environment variables:**

| Variable           | Required | Default     | Description                  |
|--------------------|----------|-------------|------------------------------|
| `LEX_BOT_ID`       | yes*     | –           | Lex V2 Bot ID                |
| `LEX_BOT_ALIAS_ID` | yes*     | –           | Lex V2 Bot Alias ID          |
| `LEX_LOCALE_ID`    | no       | `en_US`     | NLU locale                   |
| `AWS_REGION`       | no       | `us-east-1` | AWS region of the bot        |

\* Can be set per-test-case in `lex_test_cases.json` instead of globally.

**`.env` file (repo root):**

```bash
LEX_BOT_ID=ABCDE12345
LEX_BOT_ALIAS_ID=TSTALIASID
LEX_LOCALE_ID=en_US
AWS_REGION=eu-west-2
```

---

## Running Tests

### Shell script (recommended)

Works from the repo root **or** from inside `lex_testing/`:

```bash
# from repo root:
./lex_testing/run_lex_tests.sh

# from inside lex_testing/:
./run_lex_tests.sh
```

To pass extra pytest flags (e.g. filter by name, stop on first failure):

```bash
PYTEST_ARGS="-k INTENT-001 -x" ./lex_testing/run_lex_tests.sh
```

### Direct pytest

```bash
# from repo root:
pytest -s -v lex_testing/test_lex_bots.py

# run a single test by name:
pytest -s -v -k "MULTI-002" lex_testing/test_lex_bots.py
```

---

## Test Case Format (`lex_test_cases.json`)

All test cases live in `lex_testing/lex_test_cases.json`.

### Single-turn test case

Used when the full validation can be done in one Lex API call.

```json
{
  "name": "INTENT-001 CheckBalance – canonical utterance",
  "description": "Validates 'Check my balance' maps to CheckBalance.",
  "bot_id": "ABCDE12345",
  "bot_alias_id": "TSTALIASID",
  "locale_id": "en_US",
  "input_text": "Check my balance",
  "expected_intent": "CheckBalance",
  "expected_intent_state": "InProgress",
  "expected_dialog_state": "ElicitSlot",
  "expected_elicited_slot": "accountType",
  "expected_slots": {},
  "expected_message_fragment": "account",
  "expected_session_attributes": {},
  "expected_active_contexts": []
}
```

### Multi-turn test case

Used when the validation spans multiple back-and-forth conversation turns.  
Session continuity is preserved automatically using a shared `session_id`.

```json
{
  "name": "MULTI-001 CheckBalance – slot elicitation",
  "description": "Bot solicits accountType then fulfils.",
  "bot_id": "ABCDE12345",
  "bot_alias_id": "TSTALIASID",
  "locale_id": "en_US",
  "initial_session_attributes": { "customerTier": "premium" },
  "turns": [
    {
      "input_text": "I want to check my balance",
      "expected_intent": "CheckBalance",
      "expected_dialog_state": "ElicitSlot",
      "expected_elicited_slot": "accountType",
      "expected_message_fragment": "account"
    },
    {
      "input_text": "My checking account",
      "expected_intent": "CheckBalance",
      "expected_slots": { "accountType": "checking" },
      "expected_dialog_state": "Fulfilled",
      "expected_intent_state": "Fulfilled",
      "expected_message_fragment": "balance"
    }
  ]
}
```

### Supported assertion fields (per test case or per turn)

| Field | Type | Description |
|-------|------|-------------|
| `expected_intent` | string | Intent name Lex must return |
| `expected_intent_state` | string | `InProgress` / `Fulfilled` / `ReadyForFulfillment` / `Failed` |
| `expected_dialog_state` | string | `ElicitSlot` / `ConfirmIntent` / `Fulfilled` / `Close` |
| `expected_elicited_slot` | string | Name of the slot Lex is currently asking for |
| `expected_slots` | object | `{slotName: interpretedValue}` – verified as a subset |
| `expected_message_fragment` | string | Case-insensitive substring of the bot's response |
| `expected_session_attributes` | object | `{key: value}` – verified as a subset |
| `expected_active_contexts` | array | Context names that must appear in `activeContexts` |

### Top-level test case fields

| Field | Type | Description |
|-------|------|-------------|
| `bot_id` | string | Overrides `LEX_BOT_ID` env var |
| `bot_alias_id` | string | Overrides `LEX_BOT_ALIAS_ID` env var |
| `locale_id` | string | Overrides `LEX_LOCALE_ID` env var |
| `initial_session_attributes` | object | Injected into the Lex session on turn 1 |
| `input_text` | string | Single-turn input (omit when using `turns`) |
| `turns` | array | Multi-turn conversation (omit when using `input_text`) |

---

## Test Case Coverage

The included `lex_test_cases.json` covers the following sections:

| Section | Prefix | What is tested |
|---------|--------|----------------|
| Intent recognition | `INTENT-` | Single-turn utterance → correct intent; FallbackIntent for unknown inputs |
| One-shot slot filling | `SLOT-` | All required slots extracted from a rich single utterance |
| Multi-turn slot elicitation | `MULTI-` | Bot prompts for each slot in sequence; full conversation paths |
| Fallback recovery | `FALLBACK-` | Bot recovers after 1 or 3 unrecognised inputs; escalates to agent |
| Intent redirection | `REDIRECT-` | Completed intent chains to a new intent in the same session |
| Session attributes | `SESSION-` | Attributes injected at start; persisted / set by Lambda hook |
| Slot edge cases | `SLOT-EDGE-` | Word-form numbers, invalid values, re-elicitation |
| Localisation | `L10N-` | `en_GB` locale with British English synonyms |
| Confirmation flows | `CONFIRM-` | Yes / No / ambiguous responses at ConfirmIntent step |
| Active contexts | `CONTEXT-` | Post-intent context names; context-aware follow-up intents |

---

## Adding New Test Cases

1. Open `lex_testing/lex_test_cases.json`.
2. Append a new JSON object following the format above.
3. Use a unique `"name"` following the `SECTION-NNN Description` convention.
4. Run the suite to verify:

```bash
./lex_testing/run_lex_tests.sh
```

> **Tip:** If `bot_id` / `bot_alias_id` are set to `"REPLACE_WITH_BOT_ID"` (the placeholder values), the test will be **skipped** — not failed — until real IDs are configured.

---

## GitHub Actions Integration

Add the following secrets to your GitHub repository:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `LEX_BOT_ID`
- `LEX_BOT_ALIAS_ID`

Create `.github/workflows/lex_regression_tests.yml`:

```yaml
name: Lex Regression Tests

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]

permissions:
  id-token: write
  contents: read

jobs:
  test-lex-bots:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GithubActionsRole
          aws-region: eu-west-2

      - name: Run Lex regression tests
        env:
          LEX_BOT_ID: ${{ secrets.LEX_BOT_ID }}
          LEX_BOT_ALIAS_ID: ${{ secrets.LEX_BOT_ALIAS_ID }}
          LEX_LOCALE_ID: en_US
          AWS_REGION: eu-west-2
        run: |
          chmod +x lex_testing/run_lex_tests.sh
          ./lex_testing/run_lex_tests.sh
```
