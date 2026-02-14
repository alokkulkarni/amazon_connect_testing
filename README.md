# Amazon Connect Voice Flow Automation

This project provides an automated Python framework to test Amazon Connect voice flows and contact scenarios.

## Features
-   **Automated Voice Testing:** Initiates mock or real calls using Amazon Connect's `StartOutboundVoiceContact`.
-   **Configurable Test Cases:** Define test inputs, expected flows, and attributes in a JSON file.
-   **GitHub Actions Integration:** Pre-configured CI/CD workflow to run tests automatically on push or PR.
-   **Validation:** Checks successful initiation and basic status (extensible for CloudWatch log validation).

## Setup

1.  **Install Python Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Environment Variables:**
    -   Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
        ```
    -   Edit `.env` with your values:
        -   `AWS_REGION`: The AWS region (e.g., `us-east-1`).
        -   `CONNECT_INSTANCE_ID`: The ID or ARN of your Amazon Connect instance.
        -   `CHIME_SMA_ID`: The ID of the Chime SIP Media Application used to drive the call.
        -   `CHIME_PHONE_NUMBER`: The source phone number (provisioned in Chime) to dial from.
        -   `MOCK_AWS`: Set to `true` (default) for local testing without credentials, or `false` to make real API calls.

    -   **For Local Real AWS Testing:**
        -   Ensure you have configured AWS credentials locally using `aws configure` or by setting environment variables.
        -   The script will automatically pick up your local AWS profile.

## Usage

### 1. Define Test Cases
Edit `test_cases.json`. Note that `destination_phone` should be your Amazon Connect claimed phone number.
```json
[
  {
    "name": "Account Balance",
    "destination_phone": "+18005550100",
    "input_speech": "I want to check my balance",
    "expected_queue": "AccountsQueue"
  }
]
```

### 2. Run Tests Locally
Run the test script using `pytest` or the helper script:

```bash
./run_tests.sh
```
To switch between mock and real modes, simply edit the `MOCK_AWS` variable in your `.env` file.

### 3. Test Output
The script generates output indicating PASS/FAIL status for each test case.
-   **PASS:** Call initiated successfully, and basic validation (like returned ContactId) passed.
-   **FAIL:** API error or assertion failure.

## How it works (Inbound Simulation)
The script uses **AWS Chime SDK** to initiate an outbound call *to* Amazon Connect. This simulates a real customer calling in.
1.  **Chime SDK** places a SIP call to the Connect phone number.
2.  The script passes the `input_speech` to the Chime SIP Media Application.
3.  The Chime Application (via Lambda) uses the `Speak` action (TTS) to "talk" to the Connect bot.
4.  The script then verifies if the call was routed to the expected queue in Amazon Connect.

**Note:** If this folder is the root of your repository, the `.github` folder is already in the correct place. If this folder is a subdirectory within a larger repository, you must move the `.github` folder to the root of your repository for GitHub Actions to detect it.

### Setting up CI/CD
1.  Push this folder to your GitHub repository.
2.  Go to **Settings > Secrets and variables > Actions** in your repo.
3.  Add the following secrets:
    -   `AWS_ROLE_ARN`: The ARN of the IAM role to assume (requires OIDC trust with your GitHub repo).
    -   `AWS_REGION`
    -   `CONNECT_INSTANCE_ID`
4.  The workflow will run automatically on push to `main`.
    -   It defaults to `MOCK_AWS: 'true'`.
    -   To run real tests, update the workflow file to set `MOCK_AWS: 'false'` and ensure `AWS_ROLE_ARN` is configured.


## Advanced Validation (Future Work)
To validate flow logic more deeply (e.g., "did the user hear the prompt?"), consider:
-   Integrating with Amazon CloudWatch Logs to parse execution paths.
-   Using Amazon Connect Contact Trace Records (CTR) streams.
-   Using a telephony testing tool (like Twilio) to place actual calls and verify audio.

## Troubleshooting

### Common Errors

1.  **Concurrent call limits breached**:
    *   AWS Chime SDK Sandbox accounts often have a limit of 1 concurrent call.
    *   The test script includes retry logic (3 retries, 30s apart).
    *   If you see this error, wait 1-2 minutes for previous calls to fully disconnect.

2.  **Call record not found in Connect (SearchContacts)**:
    *   Amazon Connect's `SearchContacts` API has a natural indexing latency (1-3 minutes).
    *   The test script retries searching for up to 5 minutes.
    *   If this fails consistently, verify:
        *   Your Connect instance has `Contact Lens` enabled (optional, but helps with search).
        *   The IAM user has `connect:SearchContacts` permission.
        *   The `CHIME_PHONE_NUMBER` matches what Connect sees as the Caller ID.

3.  **No contacts found in queue**:
    *   Ensure the `test_cases.json` maps to the correct **Queue Name** in your Connect instance.
    *   Ensure the Contact Flow logic actually routes to that queue.
