# AWS Chime SDK Setup Guide for Voice Automation

This guide explains how to set up the required AWS Chime SDK infrastructure to support the inbound voice testing script.

## Overview
The automation uses a **Chime SIP Media Application (SMA)** to act as a "Virtual Customer".
1.  The Python script calls the Chime API (`create_sip_media_application_call`).
2.  The Chime SMA invokes a **Lambda function**.
3.  The Lambda function instructs Chime to **Speak** the text to Amazon Connect.

## Prerequisites
*   AWS Account with Admin access (or permissions for Chime and Lambda).
*   The `chime_handler_lambda.py` file from this project.

---

## Step 1: Create the Lambda Function
This function will handle the call logic (TTS).

**Region Note:** Create this Lambda in the **same region** where you plan to create the Chime SMA (e.g., `eu-central-1`).

1.  Log in to the **AWS Console** and go to **Lambda**.
2.  Click **Create function**.
3.  **Name**: `ConnectAutomation-ChimeHandler` (or similar).
4.  **Runtime**: `Python 3.9` (or newer).
5.  **Code**:
    *   Copy the contents of `amazon_connect_testing/chime_handler_lambda.py` into the code editor.
    *   Or zip and upload the file.
6.  **Deploy** the function.
7.  **Note the ARN** of the function (e.g., `arn:aws:lambda:us-east-1:123456789012:function:ConnectAutomation-ChimeHandler`).

## Step 2: Create a SIP Media Application (SMA)

**Critical Requirement:** The SIP Media Application and the Lambda function **MUST** be in the same AWS Region.
*   If you chose `eu-central-1` (Frankfurt) for your Chime Region, you must create the Lambda in `eu-central-1`.
*   If you create the Lambda in London and the SMA in Frankfurt, it will **not** work.

1.  Go to the **Amazon Chime SDK** console.
2.  In the navigation pane, under **Voice**, choose **SIP media applications**.
3.  Click **Create SIP media application**.
4.  **Name**: `ConnectAutomation-SMA`.
5.  **AWS Region**: Select the same region as your Lambda (e.g., `us-east-1`).
6.  **SIP media application endpoint**:
    *   Select **Lambda function**.
    *   Choose the `ConnectAutomation-ChimeHandler` function you created in Step 1.
7.  Click **Create**.
8.  **Note the ID** of the SMA (e.g., `78901234-5678-9012-3456-789012345678`). This is your `CHIME_SMA_ID`.

## Step 3: Provision a Phone Number

**Region Note:** If you cannot find available phone numbers in your preferred region (e.g., `eu-west-2`), or if your enterprise blocks `us-east-1`, you can use any of the following regions that support Chime SDK Voice:

*   **Europe (Frankfurt):** `eu-central-1` (Recommended alternative for EU)
*   **US West (Oregon):** `us-west-2`
*   **Canada (Central):** `ca-central-1`
*   **Asia Pacific:** `ap-southeast-1` (Singapore), `ap-southeast-2` (Sydney), `ap-northeast-1` (Tokyo)

**To switch regions:**
1.  In the AWS Console, switch to one of the regions above (e.g., `eu-central-1`).
2.  Perform **Step 2 (Create SMA)** and **Step 3 (Provision Number)** in that new region.
3.  Update your `.env` file with `CHIME_AWS_REGION=eu-central-1` (or whichever you chose).

1.  In the Amazon Chime SDK console, under **Voice**, choose **Phone numbers**.
2.  Click **Order phone numbers**.
3.  **Type**: `Local` or `Toll-free`.
4.  Follow the prompts to provision a number.
5.  **Note the Phone Number** (e.g., `+15550100`). This is your `CHIME_PHONE_NUMBER`.

## Step 4: Associate Number with SMA

1.  Select the phone number you just provisioned.
2.  Choose **Assign**.
3.  Assign it to the **SIP media application** you created (`ConnectAutomation-SMA`).

## Step 5: Update Permissions (Important)

Your Lambda function needs permission to be invoked by the Chime service. The console usually adds this automatically, but if you encounter issues:
*   Go to your Lambda function -> **Configuration** -> **Permissions**.
*   Ensure there is a Resource-based policy allowing `chime.amazonaws.com` to invoke the function.

Also, the Identity calling the script (your local user or CI/CD role) needs the `chime:CreateSipMediaApplicationCall` permission.

## Step 6: Configure Your Environment

Update your `.env` file with the IDs you collected.
*   **Important:** If you set up Chime in a different region (like `eu-central-1`), you must specify that region for the Chime Client in your code or environment.

1.  Add `CHIME_AWS_REGION` to your `.env` (if different from `AWS_REGION` which points to Connect).

```bash
AWS_REGION=eu-west-2              # Region where Amazon Connect is
CHIME_AWS_REGION=eu-central-1     # Region where Chime SDK is (e.g., Frankfurt)
CHIME_SMA_ID=your-sma-id
CHIME_PHONE_NUMBER=your-phone-number
```

## Step 7: Amazon Connect Setup (The Destination)

**Crucial Distinction:**
*   **Chime Number (`CHIME_PHONE_NUMBER`)**: Acts as the **Caller** (the "Virtual Customer"). This is the number we provisioned in Step 3.
*   **Connect Number (`destination_phone`)**: Acts as the **Callee** (the "Bot"). This is the number your customers normally call.

To complete the loop, you need a number in Amazon Connect that receives these test calls:

1.  Log in to your **Amazon Connect Instance**.
2.  Go to **Phone Numbers** -> **Claim a number** (if you haven't already).
3.  **Assign a Contact Flow**:
    *   Edit the phone number configuration.
    *   Under **Contact flow / IVR**, select the specific flow you want to test (e.g., "Sample inbound flow" or your custom "Account Balance" flow).
    *   Save.
4.  **Update Test Cases**:
    *   Open `test_cases.json` in this project.
    *   Update the `destination_phone` field with this **Amazon Connect Phone Number**.

## Step 8: Testing

Run the test script in "Real" mode (MOCK_AWS=false):
```bash
./run_tests.sh
```
**What happens:**
1.  The script tells Chime (`CHIME_PHONE_NUMBER`) to dial the Connect Number (`destination_phone`).
2.  The call travels over the PSTN (Public Switched Telephone Network) simulation.
3.  Amazon Connect receives the call and triggers the Contact Flow you assigned in Step 7.
4.  Chime "speaks" the text.
5.  Connect responds.
