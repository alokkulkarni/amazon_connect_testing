# UPDATE REQUIRED: Chime SMA Handler Lambda

The test logs indicate that the call is hanging up too quickly, preventing the test script from verifying that the call reached the Amazon Connect queue.

## Issue
The Chime SIP Media Application Lambda was hanging up immediately after the `Speak` action completed. This disconnected the call before the validation logic could run.

## Fix
The `chime_handler_lambda.py` file has been updated to include a **60-second pause** after speaking. This keeps the call active, allowing the test script to poll the Amazon Connect queue metrics successfully.

## Action Required
Please redeploy the updated code to your AWS Lambda function:

1. Go to the AWS Console > Lambda > Functions > **ChimeSMAHandler** (or your function name).
2. Copy the content of `amazon_connect_testing/chime_handler_lambda.py`.
3. Paste it into the Lambda code editor (replacing the existing code).
4. Click **Deploy**.

Once deployed, run the tests again:
```bash
cd amazon_connect_testing
./run_tests.sh
```
