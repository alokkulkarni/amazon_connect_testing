# Click-to-Call with Authenticated Context (Zero ID&V)

This document describes how to build a **Secure Click-to-Call** solution where an authenticated user in your mobile/web app can call your Amazon Connect contact center over VoIP, passing their identity securely so they don't need to answer security questions (ID&V) again.

## Architecture Overview

1.  **Client App (Web/Mobile)**: Authenticates the user (e.g., Cognito, Auth0).
2.  **Backend Service**: Validates the auth token and initiates the Chime SDK session.
3.  **Amazon Chime SDK**: Handles the VoIP audio from the client app.
4.  **SIP Media Application (SMA)**: Bridges the VoIP session to a SIP call.
5.  **Amazon Connect**: Receives the call via SIP (Voice Connector) with **User Context** injected into SIP Headers.

---

## Step 1: The Client Application (Frontend)

Your app (React, iOS, Android) uses the **Amazon Chime SDK Client Library**.

1.  **User Login**: User logs in to your app. You have their `UserId` and a valid JWT.
2.  **"Call Support" Click**:
    *   App sends a request to your Backend: `POST /start-call { token: jwt }`.
3.  **Join Session**:
    *   Backend returns Chime Meeting credentials (`MeetingId`, `AttendeeId`, `JoinToken`).
    *   App initializes `DefaultMeetingSession` and starts audio.

## Step 2: The Backend Service (Middleware)

This is the security gatekeeper.

1.  **Validate Token**: Ensure the request comes from a logged-in user.
2.  **Create Chime Meeting**:
    *   Call `chime.createMeeting()`.
    *   Call `chime.createAttendee()`.
3.  **Store Context**:
    *   Save the mapping of `MeetingId` -> `CustomerId` in a DynamoDB table or Cache.
    *   *Why?* We need to look this up later when the call reaches the SIP layer.

## Step 3: Bridging to Telephony (SIP Media Application)

To get the audio from the "Chime Meeting" (VoIP) into "Amazon Connect" (Phone Network), we use a **SIP Media Application (SMA)**.

1.  **Join the Meeting**:
    *   Your backend calls `chime.createSipMediaApplicationCall()`.
    *   Target: The **Amazon Chime SDK Voice Connector** associated with Amazon Connect.
    *   **Crucial Step**: In the `SipHeaders` or `Arguments` of this call, inject the `CustomerId` or a signed `ContextToken`.

## Step 4: Amazon Connect (The Destination)

Amazon Connect receives the call via the **Voice Connector**.

1.  **Inbound Flow**: The call hits your "Inbound Flow".
2.  **Extract Context**:
    *   Use a Lambda function in the flow to read the **SIP Headers** (`User-to-User` or custom headers).
    *   *Alternatively*: Use the caller's ID to look up the `MeetingId` context you stored in Step 2.
3.  **Set Attributes**:
    *   Lambda returns `{ "auth_status": "verified", "customer_id": "12345" }`.
    *   Use the **Set Contact Attributes** block to store these.
4.  **Routing**:
    *   Check `auth_status`.
    *   **If Verified**: Route directly to "Premium Queue" or "Personal Banker". Play prompt: *"Welcome back, John. Connecting you now..."*
    *   **If Failed**: Route to standard ID&V flow.

## Step-by-Step Implementation Guide

### 1. Setup Amazon Connect Voice Connector
1.  Go to **AWS Console > Amazon Chime SDK > Voice Connectors**.
2.  Create a new Voice Connector (e.g., `Connect-Inbound-VC`).
3.  Enable **Termination** (Inbound calling).
4.  Allow the IP ranges of your SIP Media Application.

### 2. Configure SIP Media Application (SMA)
1.  Create a Lambda (`SipBridgeLambda`).
2.  Create an SMA pointing to this Lambda.
3.  **Lambda Logic**:
    *   Event: `NEW_OUTBOUND_CALL`.
    *   Action: `JoinChimeMeeting`.
    *   This action connects the SIP call (going to Connect) with the Chime Meeting (where the user is).

### 3. Connect Flow Logic
1.  Create a Lambda (`ExtractAuthContext`).
2.  In Connect Flow:
    *   **Invoke AWS Lambda Function**: `ExtractAuthContext`.
    *   **Check Attribute**: `$.External.auth_status`.
    *   **Branch**: If `verified`, skip prompt "Please enter your account number".

## Security Considerations
*   **SIP Headers**: Pass a **signed JWT** in the SIP `X-Auth-Token` header rather than raw PII.
*   **Validation**: The receiving Lambda (in Connect) must verify the signature of the token before trusting the `customer_id`.
*   **Encryption**: Ensure TLS is enabled on the Voice Connector.

## Summary

| User Action | System Action | Data Flow |
| :--- | :--- | :--- |
| **User Clicks Call** | App calls Backend | `Token` sent to API |
| **Backend** | Creates Meeting + SMA Call | `Context` saved / injected |
| **VoIP Audio** | Chime SDK <-> SMA | Audio flows over internet |
| **Connect Routing** | SMA -> Voice Connector | `X-Auth-Token` passed in SIP |
| **Agent Screen** | Connect Flow | "Verified Customer: John Doe" |
