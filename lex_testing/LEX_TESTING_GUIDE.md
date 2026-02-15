# Amazon Lex Bot Regression Testing

This directory contains automated regression tests for Amazon Lex bots.
The tests validate that your bot correctly recognizes intents, slots, and responds with expected messages for given inputs.

## Setup

1. **Prerequisites**
   - Python 3.8+
   - AWS CLI configured or environment variables set.
   - Required packages: `boto3`, `pytest`, `python-dotenv`

2. **Installation**
   ```bash
   pip install boto3 pytest python-dotenv
   ```

3. **Configuration**
   - You need the **Bot ID** and **Bot Alias ID** for the Lex bot you want to test.
   - You can set these as environment variables or define them in each test case in `test_cases.json`.

   **Environment Variables:**
   ```bash
   export LEX_BOT_ID="your-bot-id"
   export LEX_BOT_ALIAS_ID="your-bot-alias-id"
   export AWS_REGION="us-east-1"  # Defaults to us-east-1
   ```

## Running Tests

Run the included shell script from the project root (`amazon_connect_testing/`):

```bash
./lex_testing/run_lex_tests.sh
```

Or run directly with pytest:

```bash
pytest -s -v lex_testing/test_lex_bots.py
```

## Test Cases Definition

Test cases are defined in the root `test_cases.json` file.
Add entries with `"type": "lex"`.

**Example:**
```json
{
  "name": "Check Balance Intent",
  "type": "lex",
  "description": "Verify user can ask for balance",
  "bot_id": "OPTIONAL_OVERRIDE_BOT_ID",
  "bot_alias_id": "OPTIONAL_OVERRIDE_ALIAS_ID",
  "locale_id": "en_US",
  "input_text": "What is my account balance?",
  "expected_intent": "CheckBalance",
  "expected_slots": {
    "AccountType": "Checking"
  },
  "expected_message_fragment": "Your checking account balance is"
}
```

- `bot_id` / `bot_alias_id`: Optional if environment variables are set.
- `expected_intent`: Required. The name of the intent Lex should recognize.
- `expected_slots`: Optional. Key-value pairs of slot names and their resolved values.
- `expected_message_fragment`: Optional. A substring expected in the bot's response text.

## GitHub Actions Integration

To run these tests in GitHub Actions, create a workflow `.github/workflows/lex_regression_tests.yml`.

Ensure you add the following secrets to your GitHub repository:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `LEX_BOT_ID` (Default bot ID)
- `LEX_BOT_ALIAS_ID` (Default alias ID)

### Workflow Example

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
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'

    - name: Install Dependencies
      run: |
        python -m pip install --upgrade pip
        pip install boto3 pytest python-dotenv

    - name: Configure AWS Credentials
      uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::YOUR_ACCOUNT_ID:role/GithubActionsRole
        aws-region: us-east-1

    - name: Run Lex Tests
      env:
        LEX_BOT_ID: ${{ secrets.LEX_BOT_ID }}
        LEX_BOT_ALIAS_ID: ${{ secrets.LEX_BOT_ALIAS_ID }}
      run: |
        chmod +x amazon_connect_testing/lex_testing/run_lex_tests.sh
        cd amazon_connect_testing && ./lex_testing/run_lex_tests.sh
```
