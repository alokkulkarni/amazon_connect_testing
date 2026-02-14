# Verified Outbound Calling (Anti-Spoofing Trust Layer)

This document details how to implement a **Verified Business Call** solution. When an agent dials a customer from Amazon Connect, the customer's banking app immediately receives a secure notification confirming the caller's identity. This prevents spoofing and builds trust.

## The Problem
Malicious actors often spoof bank phone numbers. Customers are trained not to answer or trust unknown callers.

## The Solution: "Push-Then-Dial"
Instead of the agent simply dialing the phone network, the dialing action is orchestrated to send a cryptographic proof of identity to the customer's mobile app *seconds before* or *during* the ringing phase.

## Architecture Components

1.  **Agent Desktop (Custom CCP)**: A web interface where the agent clicks "Call Customer".
2.  **Orchestration Layer (Backend)**: A Lambda function that coordinates the notification and the call.
3.  **Push Notification Service (AWS Pinpoint/SNS)**: Sends the high-priority data message.
4.  **Mobile App (iOS/Android)**: Receives the push and displays a "Verified Calling Screen" (using CallKit or Overlay).
5.  **Amazon Connect**: Places the actual telephony call.

---

## Step-by-Step Implementation Guide

### Step 1: Agent Initiates Call (The Trigger)
*Do not use the standard Connect dial pad.* Embed a "Click-to-Call" button in your CRM or Agent Workspace.

#### Option A: AWS Agent Workspace (Native)
If your agents use the standalone AWS Agent Workspace:
1.  **Custom App Integration**:
    *   Build a simple web widget (React/HTML) that displays the customer's "Verified Status".
    *   Configure this as a **Third-Party Application** within the Agent Workspace.
    *   When the agent opens a task or profile, this widget loads.
2.  **The Button**:
    *   The widget displays a "Start Verified Call" button.
    *   **Action**: `onClick` -> Calls your backend API (`/initiate-verified-call`).
    *   **Feedback**: The widget shows "Notifying Customer..." -> "Calling...".

#### Option B: Microsoft Dynamics 365
If your agents work inside Dynamics 365 (with the Connect CCP embedded via Channel Integration Framework - CIF):
1.  **Command Bar Button**:
    *   Use the **Ribbon Workbench** or Power Apps Command Designer to add a new button "Verified Call" to the "Contact" or "Account" form.
2.  **JavaScript Resource**:
    *   Attach a JavaScript function to this button.
    *   **Logic**:
        ```javascript
        function initiateVerifiedCall(primaryControl) {
            var formContext = primaryControl.getFormContext();
            var customerId = formContext.data.entity.getId();
            var agentId = Xrm.Utility.getGlobalContext().userSettings.userId;
            
            // 1. Call your Orchestration Backend
            fetch("https://api.yourbank.com/initiate-verified-call", {
                method: "POST",
                body: JSON.stringify({ customerId: customerId, agentId: agentId })
            }).then(() => {
                Xrm.Navigation.openAlertDialog({ text: "Notification sent. Dialing now..." });
            });
        }
        ```
3.  **Disable Standard Dialing**: (Optional) Disable the default "click-to-dial" on the phone number field to ensure agents use the verified path.

### Step 2: Orchestrator Logic (The Backend)
The backend (Lambda) performs two parallel actions to ensure speed.

1.  **Lookup**: Retrieve customer's registered device token and Agent's metadata (Name, Photo URL, EmployeeID).
2.  **Action A (Notify)**: Send a **High-Priority Data Push** to the customer's device.
    *   Payload: `{ type: "call_verification", agent_name: "John Doe", agent_photo: "url", reason: "Fraud Check", timestamp: 12345 }`
3.  **Action B (Dial)**: Call Amazon Connect API `StartOutboundVoiceContact`.
    *   *Optimization*: You can add a 1-2 second delay here to ensure the Push arrives before the first ring.

### Step 3: The Mobile App (The Verification)
The app receives the push notification.

**On iOS (using CallKit):**
1.  **Receive Push**: The app wakes up in the background (VoIP Push).
2.  **Register Call**: The app tells iOS CallKit: "Incoming call from Bank - Agent John".
3.  **Display**: When the actual phone call rings (seconds later), the standard iOS Call Screen displays **"Verified: Bank - Agent John"** with your logo, instead of just a phone number.

**On Android:**
1.  **Receive Push**: App service wakes up.
2.  **Overlay/Notification**: Display a full-screen "Incoming Call" overlay or a high-priority banner saying "Incoming Call from your Personal Banker".

### Step 4: The Conversation
1.  The customer answers the phone, already knowing who is on the line.
2.  Trust is established immediately.

---

## Performance & Guarantees

### Why Push Notifications?
*   **Security**: Unlike SMS, Push messages are encrypted from your server to the app. They cannot be intercepted or spoofed by SS7 network hacks.
*   **Speed**: High-priority FCM/APNS messages typically arrive in <500ms.
*   **Experience**: It integrates with the native OS dialer screen (via CallKit/ConnectionService), making it look like an internal feature.

### Guaranteed Delivery Strategies
1.  **TTL (Time to Live)**: Set a short TTL (e.g., 30 seconds). If the phone is offline, don't deliver old "incoming call" alerts later.
2.  **Fallback**: If the user does not have the app installed, fall back to **Flash SMS** or **WhatsApp Template Message** (e.g., "Bank Alert: We are calling you now from 0800-123-456").

## Enforcement: Blocking Unverified Calls

What if an agent tries to bypass the process and uses the standard dialer? You must enforce the "Push-First" rule.

### Level 1: Disable Manual Dialing (Recommended)
The most effective control is to remove the ability for agents to manually type a number.
1.  **CCP Configuration**: When initializing the Amazon Connect Streams API (in your Agent Workspace or CRM), set `pageOptions.enableKeypad` to `false`.
    ```javascript
    connect.core.initCCP(containerDiv, {
       pageOptions: {
          enableKeypad: false // Hides the dialpad
       }
    });
    ```
2.  **Result**: Agents **cannot** initiate a call except via your "Verified Call" button (which calls your API).

### Level 2: Intercept CRM Clicks (Dynamics 365)
Agents often click the phone number field in the CRM (Click-to-Act).
1.  **Channel Integration Framework (CIF)**:
    *   Use `Microsoft.CIFramework.addHandler("onclicktoact", handlerFunction)`.
    *   **How the dialing actually happens:**
        In this secure design, the browser/CRM does **not** place the call directly. Instead, your JavaScript handler calls your Backend API. The Backend API then instructs Amazon Connect to place the call (via `StartOutboundVoiceContact`).
        *   **Agent Experience**: The agent clicks the number. A "Notifying..." spinner appears. Then their softphone (CCP) auto-answers, bridging them to the outbound leg.
    *   **Handler Logic**:
        ```javascript
        function handlerFunction(eventData) {
            // 1. Parse the event data to get phone number and ID
            var context = JSON.parse(eventData);
            var phoneNumber = context.value;
            var customerId = context.name; // Depends on your CRM config
            
            // 2. Call your Secure Backend (The Orchestrator)
            // We do NOT use the standard softphone dial function here.
            // The Backend will send the Push, wait 1s, then call Connect API.
            fetch("https://api.yourbank.com/initiate-verified-call", {
                method: "POST",
                body: JSON.stringify({ phone: phoneNumber, customerId: customerId })
            }).then(response => {
                // Optional: Show feedback in CRM
                Xrm.Navigation.openAlertDialog({ text: "Verified Call Initiated." });
            });

            // 3. Return Promise to stop standard processing (if applicable in your CIF version)
            return Promise.resolve(context);
        }
        ```

### Level 3: Flow Compliance (The Safety Net)
If you must allow manual dialing (e.g., for non-customer calls), use the **Outbound Whisper Flow** to catch violations.
1.  **Configuration**: In Amazon Connect > Queues, set the "Outbound caller ID number" flow to a specific **Outbound Whisper Flow**.
2.  **Flow Logic**:
    *   **Check Attribute**: Check for a user-defined attribute `verified_call`.
        *   *Note:* Your API sets this to `true`. Manual dialing does not set it.
    *   **If False (Manual Dial)**:
        *   **Log**: Record a "Compliance Violation" in CloudWatch/CTR.
        *   **Alert**: Play a whisper to the agent: *"Warning: You performed an unverified call."*
        *   *(Optional)* **Terminate**: Disconnect the call immediately (Note: This results in a "phantom ring" for the customer, so use with caution).

## Sequence Diagram (Hybrid Approach: Dynamics 365 + Streams API)

This diagram shows the complete flow where the agent initiates the call from Dynamics 365, the customer receives the "Verified" push notification, and then the agent's softphone places the call so they can hear the ringing.

```text
                                        [Orchestration]     [Push Service]      [Customer]        [Amazon]
[Agent / Dynamics 365]                     [Backend]          (APNS/FCM)        [Mobile App]      [Connect]
      |                                        |                  |                  |                |
      | 1. Click "Verified Call" (CIF)         |                  |                  |                |
      |--------------------------------------->|                  |                  |                |
      |                                        |                  |                  |                |
      |                                        | 2. Send Push     |                  |                |
      |                                        |----------------->|                  |                |
      |                                        |                  | 3. Wake Up App   |                |
      |                                        |                  |----------------->|                |
      |                                        |                  |                  | 4. Show Screen |
      |                                        |                  |                  | "Verified Call"|
      |                                        |                  |                  |                |
      | 5. Return "Success" (Push Sent)        |                  |                  |                |
      |<---------------------------------------|                  |                  |                |
      |                                        |                  |                  |                |
      | 6. JS calls agent.connect() (Streams)  |                  |                  |                |
      |---------------------------------------------------------------------------------------------->|
      |                                        |                  |                  |                |
      | 7. Softphone Rings (Outbound)          |                  |                  |                |
      |<==============================================================================================|
      |                                        |                  |                  |                |
      |                                        |                  |                  | 8. Ring Phone  |
      |                                        |                  |                  |===============>|
      |                                        |                  |                  |                |
      |                                        |                  |                  | 9. Answer      |
      |          10. Two-way Audio             |                  |                  |--------------->|
      |<=============================================================================================>|
```

### Clarification: Backend API vs. Streams API
There is often confusion between the two APIs. It is critical to understand the difference for your implementation.

1.  **`StartOutboundVoiceContact` (AWS SDK)**:
    *   This is a **Server-Side API**.
    *   It initiates a call *from Amazon Connect* to the customer.
    *   **Behavior**: The System dials the Customer. When the Customer answers, the System executes a Flow (which usually transfers the call to an Agent Queue).
    *   **Agent Experience**: The Agent sits idle. When the Customer answers, the Agent's CCP rings as if it were an *Incoming Call*. The Agent accepts and is connected.
    *   **Pros**: Absolute enforcement. The Agent cannot dial manually.
    *   **Cons**: The Agent does not hear the phone ring. They only talk if the customer answers.

2.  **`agent.connect()` (Connect Streams API)**:
    *   This is a **Client-Side (Browser) API**.
    *   It initiates a standard outbound call from the Agent's softphone.
    *   **Behavior**: The Agent hears the ringing.
    *   **Agent Experience**: Standard outbound call.
    *   **Enforcement**: Requires the "Level 2" JavaScript interception described above (JS calls Backend -> Backend sends Push -> JS dials).

### Security Requirements for `StartOutboundVoiceContact` (Backend Approach)

If you choose the Backend/Server-Side approach for strict enforcement:

1.  **IAM Permissions**:
    The IAM Role used by your Backend Lambda/Service must have:
    ```json
    {
        "Effect": "Allow",
        "Action": "connect:StartOutboundVoiceContact",
        "Resource": "arn:aws:connect:region:account:instance/instance-id"
    }
    ```

2.  **Authentication (Dynamics -> Backend)**:
    *   Since Dynamics is calling your Backend API, you must secure this link.
    *   **Method**: Use an **API Key** or **OAuth Token** (Azure AD).
    *   The Dynamics JavaScript `fetch` call must include this token in the header.

3.  **Agent Visibility in CCP**:
    *   **Yes**, the Agent sees the call, but it appears differently depending on the method.
    *   **Backend Method**: The call appears as an *Incoming Contact* (from the queue) after the customer has already picked up.
    *   **Streams Method**: The call appears as an *Outbound Contact* immediately, and the agent hears the ringing.

**Recommendation**: For the best Agent experience (hearing the ring), use the **Hybrid Approach**:
1.  Dynamics JS calls Backend (`/send-push`).
2.  Backend sends Push and returns `200 OK`.
3.  Dynamics JS *only then* calls `agent.connect()` (Streams API) to place the call.

### Security Requirements for Client-Side Streams API (`agent.connect`)

If you choose the **Hybrid Approach** (Dynamics JS calls `agent.connect()` after notifying backend), the security model is different because the *Browser* is placing the call.

1.  **Authentication (CCP Login)**:
    *   The Agent must be logged into Amazon Connect in the browser session.
    *   The `agent.connect()` function relies on the active session cookie/token established when the CCP (Contact Control Panel) was loaded.
    *   If the session has timed out, the call will fail.

2.  **Allowed Origins (CORS/Framing)**:
    *   Since your Dynamics 365 instance is hosting the Connect Streams code, you must explicit allow it.
    *   **Action**: Go to AWS Console -> Amazon Connect -> **Approved Origins**.
    *   **Add**: `https://your-org.crm.dynamics.com` (Your Dynamics 365 URL).
    *   *Why?* This allows the Connect CCP (iframe) to communicate with the parent page (Dynamics).

3.  **Agent Security Profile**:
    *   The user logged into the CCP must have a **Security Profile** with the **Outbound call** permission enabled.
    *   If this is disabled, `agent.connect()` will throw an error.

4.  **Browser Permissions**:
    *   The browser (Chrome/Edge) must have granted **Microphone Access** to the Dynamics 365 domain (or the iframe domain if isolated).
    *   This is standard WebRTC security.

5.  **Network/Firewall**:
    *   The Agent's computer must be able to reach Amazon Connect signaling endpoints and media IPs (UDP/TCP 3478, 443).
    *   This is the same requirement as using the CCP normally.
