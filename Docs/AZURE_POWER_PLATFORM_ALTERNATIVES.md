# Voice Automation on Azure & Power Platform

This document outlines how to implement a similar "Virtual Customer" voice testing solution using Microsoft Azure and Power Platform technologies instead of AWS Chime SDK.

## Architecture Mapping

The core concept remains the same: a programmable telephony service acts as the "Virtual Customer" to dial your Contact Center.

| AWS Component (Current) | Azure Equivalent | Description |
| :--- | :--- | :--- |
| **Amazon Chime SDK** | **Azure Communication Services (ACS)** | Provides the PSTN connectivity and phone numbers. |
| **SIP Media Application** | **ACS Call Automation** | The API used to control calls (answer, dial, play audio). |
| **Lambda Function** | **Azure Functions** | The serverless compute that handles call events and logic. |
| **Amazon Polly** | **Azure AI Speech** | Built-in Cognitive Service for Text-to-Speech (TTS). |
| **Amazon Transcribe** | **Azure AI Speech** | Built-in Cognitive Service for Speech-to-Text (STT). |

---

## Solution 1: Azure Communication Services (Pro-Code)

This is the direct technical equivalent to the Chime SDK solution. It allows you to build a robust, code-driven test harness.

### Step 1: Provision Resources
1.  **Create an Azure Communication Services (ACS) Resource**: Search for "Communication Services" in the Azure Portal.
2.  **Enable Managed Identity**: Allow ACS to access Cognitive Services.
3.  **Create an Azure AI Services Resource**: Required for "text-to-speech" capabilities within the call.

### Step 2: Get a Phone Number
1.  In your ACS resource, navigate to **Phone Numbers**.
2.  Click **Get** to procure a number.
    *   *Note:* ACS often has different regional inventory than AWS. You may find numbers in `UK South` (London) or other European regions more easily.
3.  Ensure the number allows **Outbound Calling**.

### Step 3: Create the "Virtual Customer" Logic (Azure Functions)
Unlike Chime (which invokes Lambda synchronously), ACS uses an **Event-Driven Webhook** model.

1.  **Create an Azure Function** (C#, Python, or Java).
2.  **Implement the Logic**:
    *   **Start Call**: Use the `CallAutomationClient` SDK to call `CreateCallAsync()`. Target your Amazon Connect phone number.
    *   **Handle Callbacks**: Create an HTTP Trigger to receive `EventGrid` events (like `CallConnected`, `PlayCompleted`).
    *   **Speak**: On `CallConnected`, call the `PlayAsync()` method, passing a `TextSource` with the text you want to speak. Azure AI Speech generates the audio automatically.

### Step 4: Validation
*   **Speech Recognition**: Use the `RecognizeAsync()` method to "listen" to the bot's response and transcribe it.
*   **Compare**: Check if the transcribed text matches your `expected_voice_prompt`.

---

## Solution 2: Power Platform (Low-Code Orchestration)

Power Platform is excellent for **triggering, managing, and reporting** on tests, though the actual "voice" part still requires the Azure setup above (since Power Automate cannot natively "speak" on a phone line).

### Step 1: The Trigger (Power Automate)
Create a **Cloud Flow** to act as the Test Runner.
1.  **Trigger**: "Recurrence" (e.g., Run every morning) or "When a record is created" (e.g., New Test Case in Dataverse).
2.  **Action**: **HTTP Request**.
    *   Call the **Azure Function** (from Solution 1) to start the test.
    *   Pass the test case data (Phone Number, Text to Speak) in the JSON body.

### Step 2: Handling Results
1.  **Action**: Parse the response from the Azure Function.
2.  **Condition**: If `Status` equals `Failed`:
    *   **Teams**: Post a message to the "QA Channel".
    *   **DevOps**: Create a Bug in **Azure DevOps** automatically.

### Step 3: Reporting (Power BI)
1.  Store test results in **Dataverse** or **SharePoint Lists**.
2.  Connect **Power BI** to this data source.
3.  Visualize "Pass Rate by Flow" or "Failure Trends".

---

## Comparison Summary

| Feature | AWS Chime SDK Solution | Azure ACS Solution |
| :--- | :--- | :--- |
| **Setup Complexity** | Medium (Lambda + Console) | Medium-High (Async Events + Webhooks) |
| **Regional Coverage** | Good (US/EU), blocked in some Enterprise settings | Excellent global coverage, often whitelisted in Enterprises using Teams |
| **Cost** | Pay-as-you-go | Pay-as-you-go |
| **Integration** | Best for AWS-hosted Contact Centers | Best if your Enterprise uses Azure/Teams |

## Recommendation
If your enterprise blocks `us-east-1` or has strict AWS limits, implementing the **Azure Communication Services** solution (running in `UK South` or `Europe West`) is a viable alternative to test your Amazon Connect flows.
