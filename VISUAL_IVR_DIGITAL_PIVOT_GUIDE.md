# Visual IVR & Digital Pivot Implementation Guide

This document details how to implement a **Visual IVR** solution that pivots customers from a traditional voice call to a rich digital experience. It also explores how this integrates with the **Click-to-Call** architecture.

## 1. Overview
**Visual IVR** replaces listening to long audio menus ("Press 1 for Sales, Press 2 for Support...") with a visual menu sent to the customer's smartphone.
**Digital Pivot** is the strategic move of shifting a voice interaction to a cheaper, faster digital channel (e.g., Chat, Self-Service Form) while keeping the context.

## 2. Architecture Components

1.  **Amazon Connect**: The contact center entry point (IVR).
2.  **AWS Lambda**: Logic to generate secure links and trigger notifications.
3.  **Amazon Pinpoint / SNS**: Service to send SMS or Push Notifications.
4.  **Visual IVR Web App**: A mobile-responsive web application (React/Vue) hosted on S3/CloudFront or Amplify.
5.  **Amazon DynamoDB**: Stores the temporary session state (Context) of the call.

---

## 3. Step-by-Step Implementation

### Phase A: The Trigger (In Amazon Connect)

1.  **Inbound Call Flow**:
    *   The customer calls your main line.
    *   **Check Mobile**: Use a Lambda function to check if the `CustomerEndpoint.Address` is a mobile number.
    *   **Offer Pivot**: Play prompt: *"To save time, you can use our visual menu on your screen. Press 1 to receive a link."*

2.  **Generate Session**:
    *   If user presses 1, invoke **Lambda (GenerateLink)**.
    *   **Logic**:
        *   Create a unique `SessionId` (GUID).
        *   Store context in **DynamoDB**: `{ SessionId: "abc-123", PhoneNumber: "+1555...", Intent: "Unknown", Timestamp: 12345 }`.
        *   Generate Short Link: `https://visual.yourbank.com/start?s=abc-123`.

3.  **Send Link**:
    *   Lambda calls **Pinpoint/SNS** to send SMS: *"Click here to access the secure menu: https://visual.yourbank.com/start?s=abc-123"*.
    *   **Connect Flow**: Play prompt *"Link sent. Please check your messages."* and optionally hang up or wait in a loop.

### Phase B: The Visual Experience (Web App)

1.  **Load App**: User clicks the link. The Web App loads and reads `?s=abc-123`.
2.  **Verify Context**: App calls API `GET /session/abc-123`.
    *   Backend validates session is valid and not expired (TTL 10 mins).
3.  **Render Menu**:
    *   Display nice buttons: "Check Balance", "Report Fraud", "Talk to Agent".
    *   *Dynamic Content*: If you know who they are (from phone number), personalize it: "Hi John, need help with your recent transaction?"

### Phase C: The Action & Re-connection

1.  **Self-Service**: User clicks "Check Balance". App calls backend API to show balance. **Call Deflected!** (Cost saving).
2.  **Escalation (Click-to-Call Integration)**:
    *   User clicks "Talk to Agent".
    *   **Context Passing**: The app now knows exactly what the user wants (e.g., "Dispute Transaction").
    *   **Action**: Initiate **Click-to-Call** (as defined in our `CLICK_TO_CALL_GUIDE.md`).
        *   Call `StartWebRTCContact` passing `Attributes: { intent: "dispute_transaction", pre_verified: "true" }`.
    *   **Routing**: Amazon Connect routes this directly to the "Disputes Team", skipping the voice IVR entirely.

---

## 4. Integration with "Click-to-Call"

Visual IVR acts as the **perfect entry point** for Click-to-Call.

| Scenario | Workflow | Benefit |
| :--- | :--- | :--- |
| **Traditional** | Call -> IVR Audio -> Wait -> Agent | Slow, frustrating, high cost. |
| **Visual IVR** | Call -> SMS -> Visual Menu -> **Self Solve** | **100% Deflection**. Fastest resolution. |
| **Visual + Click-to-Call** | Call -> SMS -> Visual Menu -> "Talk to Agent" Button -> **VoIP Call** | **Smart Routing**. Context passed. No IVR repetition. Customer is already verified if they logged in during visual session. |

### How to Link Them
1.  **Authentication**: In the Visual Web App, if the user selects a sensitive option ("Transfer Money"), prompt for Biometric/App Login.
2.  **Seamless Upgrade**: Once logged in, the "Call" button uses the **Native In-App Calling** SDK (discussed previously) to verify the call.

## 5. Security Considerations

1.  **Short-Lived Links**: Links should expire in 5-10 minutes.
2.  **One-Time Use**: Once the session is "completed", the link cannot be reused.
3.  **Phone Number Validation**: Ensure the SMS is sent ONLY to the number that called in (Ani). Do not allow user input for destination number in the voice IVR to prevent "SMS Bombing" other people.

## 7. Amazon Connect Implementation Details

This section details the specific configuration changes required in Amazon Connect.

### A. AWS Lambda Functions Required
You need to deploy two Lambda functions (Node.js/Python).

1.  **`CheckMobileNumber`**:
    *   **Input**: Phone Number (E.164).
    *   **Logic**: Uses `Amazon Pinpoint` phone number validate API or a simple regex/lookup.
    *   **Output**: `{ "isMobile": "true/false" }`.

2.  **`GenerateVisualLink`**:
    *   **Input**: Phone Number, ContactId.
    *   **Logic**:
        1.  Generates a GUID.
        2.  Writes to DynamoDB: `{ PK: GUID, Phone: input.Phone, ContactId: input.ContactId, TTL: now()+10m }`.
        3.  Calls SNS/Pinpoint to send SMS with link `https://myapp.com/?s=GUID`.
    *   **Output**: `{ "status": "sent" }`.

### B. Contact Flow Configuration

Create a new Contact Flow (or edit main entry flow):

1.  **Block: Invoke AWS Lambda Function**
    *   **Function ARN**: Select `CheckMobileNumber`.
    *   **Timeout**: 3 seconds.

2.  **Block: Check Contact Attributes**
    *   **Attribute**: `$.External.isMobile`.
    *   **Condition**: If `true`, go to "Get Input".
    *   **Condition**: If `false`, go to "Standard Audio Menu".

3.  **Block: Get Customer Input**
    *   **Prompt**: *"To use our visual menu on your smartphone, press 1. For voice, press 2."*
    *   **DTMF**: 1, 2.

4.  **Block: Invoke AWS Lambda Function** (If Pressed 1)
    *   **Function ARN**: Select `GenerateVisualLink`.
    *   **Parameters**: Pass `System.CustomerEndpoint.Address`.

5.  **Block: Play Prompt**
    *   **Text**: *"We've sent a link to your mobile. Please click it to continue."*

6.  **Block: Loop / Disconnect**
    *   You can either Disconnect (Total Deflection) or put them in a Loop checking for a signal (if building a synced experience). **Recommendation: Disconnect** for simple deflection.

## 8. Detailed ASCII Sequence Diagram

```text
+----------+      +----------------+      +------------------+      +-------------------+      +----------------+
| Customer |      | Amazon Connect |      | AWS Lambda       |      | AWS Pinpoint/SNS  |      | Web App (IVR)  |
+----+-----+      +-------+--------+      +--------+---------+      +---------+---------+      +-------+--------+
     |                    |                        |                          |                        |
     | 1. Inbound Call    |                        |                          |                        |
     +------------------->|                        |                          |                        |
     |                    |                        |                          |                        |
     |                    | 2. Invoke CheckMobile  |                          |                        |
     |                    +----------------------->|                          |                        |
     |                    | 3. Return {isMobile}   |                          |                        |
     |                    |<-----------------------+                          |                        |
     |                    |                        |                          |                        |
     | 4. "Press 1 for    |                        |                          |                        |
     |     Visual Menu"   |                        |                          |                        |
     |<-------------------+                        |                          |                        |
     |                    |                        |                          |                        |
     | 5. DTMF "1"        |                        |                          |                        |
     +------------------->|                        |                          |                        |
     |                    |                        |                          |                        |
     |                    | 6. Invoke GenerateLink |                          |                        |
     |                    +----------------------->|                          |                        |
     |                    |                        | 7. Save Session (DB)     |                        |
     |                    |                        +-- self                   |                        |
     |                    |                        |                          |                        |
     |                    |                        | 8. Send SMS Payload      |                        |
     |                    |                        +------------------------->|                        |
     |                    |                        |                          | 9. Deliver SMS         |
     |                    |                        |                          +----------------------->|
     |                    |                        |                          |                        |
     |                    | 9. Play "Link Sent"    |                          |                        |
     |                    |<-----------------------+                          |                        |
     |                    |                        |                          |                        |
     | 10. Hang Up (Voice)|                        |                          |                        |
     |<-------------------+                        |                          |                        |
     |                                             |                          |                        |
     | 11. Customer Clicks Link                    |                          |                        |
     +------------------------------------------------------------------------------------------------>|
     |                                             |                          |                        |
     |                                             | 12. GET /session?id=xyz  |                        |
     |                                             |<-------------------------+------------------------+
     |                                             |                          |                        |
     |                                             | 13. Return Context       |                        |
     |                                             +------------------------->|                        |
     |                                             |                          | 14. Render Menu        |
     |                                             |                          +-- self                 |
     |                                             |                          |                        |
     | 15. Customer Selects "Agent"                |                          |                        |
     +---------------------------------------------+------------------------->|                        |
     |                                             |                          |                        |
     |                                             |                          | 16. Start VoIP Call    |
     |                                             |                          |     (Click-to-Call)    |
     |                                             |                          |     w/ Context         |
     |                                             |                          +----------------------->|
     |                                             |                          |                        |
     +                                             +                          +                        +
```
