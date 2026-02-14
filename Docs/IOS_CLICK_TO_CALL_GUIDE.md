# iOS Click-to-Call Guide

This document answers your architecture questions and provides a guide for building the iOS client.

## 1. Do I need both SMA and Voice Connector?

**Short Answer:**
*   **Option A (Custom Bridge):** **YES.** You need both. The SMA bridges the VoIP Audio (WebRTC) to SIP, and the Voice Connector receives that SIP call into Amazon Connect.
*   **Option B (Native Connect In-App Calling):** **NO.** This is the **recommended** modern approach. Amazon Connect now handles the bridging for you automatically via the `StartWebRTCContact` API. You do **not** need to provision a separate SMA or Voice Connector.

---

## 2. Recommended Approach: Amazon Connect Native In-App Calling

This approach uses the **Amazon Chime SDK for iOS** on the frontend but simplifies the backend significantly. It natively supports passing authentication context without building a custom SIP bridge.

### Architecture
1.  **iOS App**: Uses `AmazonChimeSDK` to connect.
2.  **Backend**: Calls Connect API `StartWebRTCContact`.
3.  **Amazon Connect**: Natively handles the audio and context.

### Step-by-Step Implementation

#### 1. Authentication & Security Flow
**Q: What auth is required for the iOS app?**
A: The iOS app does **not** need AWS IAM credentials. It relies on a **Session Token** pattern:

1.  **App Login**: User logs into your app (e.g., via Cognito, Auth0, or custom backend).
2.  **Request Call**: App calls *your* Backend (e.g., `POST /start-call`) with its existing session token.
3.  **Backend Verification**: Your Backend validates the user is logged in.
4.  **Backend Call**: Your Backend (using its own IAM role) calls `connect.start_web_rtc_contact`.
5.  **Session Token**: Connect returns a `ParticipantToken` and `ConnectionData`. Your backend sends this JSON back to the iOS App.
6.  **Connect**: The iOS app uses this JSON to join the session. The `ParticipantToken` acts as the authorization for the media stream.

#### 2. Backend (Node.js/Python)
Instead of creating a Chime Meeting and SMA Call, you just call one API:

```python
# Python Backend Example (Lambda)
connect = boto3.client('connect')

def start_call(user_id, auth_token):
    response = connect.start_web_rtc_contact(
        InstanceId='your-connect-instance-id',
        ContactFlowId='your-flow-id',
        Attributes={
            'customer_id': user_id,
            'auth_status': 'verified', # Pass context directly here!
            'auth_token': auth_token
        },
        ParticipantDetails={
            'DisplayName': 'John Doe'
        }
    )
    # Return these credentials to the iOS App
    return response['ConnectionData'], response['ParticipantToken']
```

#### 3. iOS Client (Swift)

**Prerequisites:**
*   Install `AmazonChimeSDK` via Swift Package Manager.

**Data Models (JSON Mapping):**
First, define the structs to match the `ConnectionData` JSON returned by your backend.

```swift
struct StartCallResponse: Codable {
    let ConnectionData: ConnectionDataModel
}

struct ConnectionDataModel: Codable {
    let Meeting: MeetingModel
    let Attendee: AttendeeModel
    let MediaPlacement: MediaPlacementModel
}

struct MeetingModel: Codable {
    let MeetingId: String
}

struct AttendeeModel: Codable {
    let AttendeeId: String
    let JoinToken: String
}

struct MediaPlacementModel: Codable {
    let AudioFallbackUrl: String
    let AudioHostUrl: String
    let SignalingUrl: String
    let TurnControlUrl: String
}
```

**Call Manager Code:**

```swift
import AmazonChimeSDK
import AVFoundation

class CallManager {
    var meetingSession: DefaultMeetingSession?
    
    func startCall(connectionData: ConnectionDataModel) {
        // 1. Create Meeting Configuration from Backend Response
        
        let meetingId = connectionData.Meeting.MeetingId
        let attendeeId = connectionData.Attendee.AttendeeId
        let joinToken = connectionData.Attendee.JoinToken
        
        let mediaPlacement = MediaPlacement(
            audioFallbackUrl: connectionData.MediaPlacement.AudioFallbackUrl,
            audioHostUrl: connectionData.MediaPlacement.AudioHostUrl,
            signalingUrl: connectionData.MediaPlacement.SignalingUrl,
            turnControlUrl: connectionData.MediaPlacement.TurnControlUrl
        )
        
        let meeting = Meeting(externalMeetingId: meetingId, mediaPlacement: mediaPlacement, meetingId: meetingId)
        let attendee = Attendee(attendeeId: attendeeId, externalUserId: attendeeId, joinToken: joinToken)
        
        let configuration = MeetingSessionConfiguration(createMeetingResponse: CreateMeetingResponse(meeting: meeting), createAttendeeResponse: CreateAttendeeResponse(attendee: attendee))
        
        // 2. Initialize Session
        let logger = ConsoleLogger(name: "CallLogger")
        self.meetingSession = DefaultMeetingSession(configuration: configuration, logger: logger)
        
        // 3. Start Audio
        self.meetingSession?.audioVideo.start()
    }
}
```

### Official Sample Code
AWS provides a complete, open-source iOS reference app for this exact scenario:

*   **iOS Sample App:** [Amazon Connect In-App Calling iOS Sample](https://github.com/amazon-connect/amazon-connect-in-app-calling-examples/tree/main/iOS/AmazonConnectInAppCallingIOSSample)
*   **Documentation:** [Set up in-app, web, video calling](https://docs.aws.amazon.com/connect/latest/adminguide/inapp-calling.html)

---

## 3. Option A: Custom Chime Bridge (If you MUST use SMA)

If you have a specific reason to use the custom SMA + Voice Connector architecture (e.g., complex SIP manipulation before Connect), here is the flow:

1.  **Backend**:
    *   Creates a Chime Meeting (`chime.createMeeting`).
    *   Creates an SMA Call (`chime.createSipMediaApplicationCall`) to your **Voice Connector**.
    *   Joins them together.
2.  **Voice Connector**: Receives the call and sends it to Connect.

**Sample Code for this approach:**
The code is more complex because you manage the bridge.
*   **GitHub Repo:** [Amazon Chime SDK Click-to-Call](https://github.com/aws-samples/amazon-chime-sdk-click-to-call)
    *   *Note:* This sample is React-based, but the backend logic is identical for iOS.

---

## Summary Recommendation

| Requirement | Recommended Path | Why? |
| :--- | :--- | :--- |
| **Pass Auth Context** | **Native In-App Calling** | You can pass `Attributes` directly in the `StartWebRTCContact` API. No SIP headers parsing needed. |
| **Zero ID&V** | **Native In-App Calling** | Connect trusts the `Attributes` you send from your backend. |
| **Simplicity** | **Native In-App Calling** | No SMA, No Voice Connector, No SIP config. |

**Use the Native Approach (Option B).** It is purpose-built for "Click-to-Call from Authenticated App".
