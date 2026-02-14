# iOS Video Banking App with Screen Share & Privacy (AWS Chime SDK)

This document details how to build a **Video Banking** iOS application using Swift and the Amazon Chime SDK. It covers the end-to-end flow, including the Agent's experience, Screen Sharing, and critical **PII Redaction** techniques to secure sensitive data during a broadcast.

## 1. Architecture: Amazon Connect Native Video

We leverage **Amazon Connect's Native In-App, Web, and Video Calling** capabilities. Under the hood, this uses the Amazon Chime SDK, but AWS manages the infrastructure (Signaling, TURN, SFU).

*   **Mobile App (iOS)**: Uses `AmazonChimeSDK` for Swift.
*   **Orchestration**: Your Backend calls `connect:StartWebRTCContact`.
*   **Agent Desktop**: Uses the standard Amazon Connect CCP (Contact Control Panel) which now natively supports video.

---

## 2. Amazon Connect Contact Flow Setup (Step-by-Step)

**Q: Do I need a separate flow for Video?**
**A: NO.** You can use the **same** Contact Flow for both Voice and Video. The routing logic (Queues, Hours of Operation) remains identical. The only difference is enabling the *capability* for video.

### Step-by-Step Configuration:

1.  **Enable Video in Instance Settings**:
    *   Go to AWS Console -> Amazon Connect -> Instance Alias.
    *   Under **Contact flows**, ensure **"Video calling"** is checked.

2.  **Update Security Profiles (Agents)**:
    *   Go to **Users** -> **Security profiles**.
    *   Select the profile used by your agents (e.g., "Agent").
    *   Under **CCP (Contact Control Panel)**, check **"Video calling"**.
    *   *Result*: This adds the Video UI to the agent's softphone.

3.  **Configure the Contact Flow**:
    *   Open your existing Inbound Contact Flow (the one used for Voice).
    *   **No specific "Video Block" is needed**.
    *   *Routing Logic*:
        *   The call enters the flow.
        *   You can use **"Check Contact Attributes"** to see if it's a video call (check `Channel = VIDEO` or a custom attribute you pass like `call_type = video`).
        *   **Queue Transfer**: Use the standard **"Transfer to Queue"** block.
    *   *Result*: When the agent accepts the work item from the queue, Connect detects the video capability and enables the camera controls.

4.  **Routing to "Video-Enabled" Agents**:
    *   If only *some* agents have webcams/training, create a specific **Queue** (e.g., "Premium Video Support") or use **Routing Profiles**.
    *   Route video calls to this queue using the "Transfer to Queue" block.

---

## 3. iOS Swift Implementation (Video Call)

### Step A: Dependencies
Add the following to your `Podfile`:
```ruby
pod 'AmazonChimeSDK'
pod 'AmazonChimeSDKMedia'
```

### Step B: Permissions (`Info.plist`)
Add usage descriptions:
*   `NSCameraUsageDescription`: "Required for video banking."
*   `NSMicrophoneUsageDescription`: "Required to speak with your banker."

### Step C: Joining the Video Call
When the backend returns the `ConnectionData` (from `StartWebRTCContact`), initialize the session.

```swift
import AmazonChimeSDK
import AmazonChimeSDKMedia
import AVFoundation

class VideoCallManager: AudioVideoObserver {
    var meetingSession: DefaultMeetingSession?
    
    // 1. Setup Session
    func joinMeeting(connectionData: ConnectionDataModel) {
        let logger = ConsoleLogger(name: "VideoBankLogger")
        
        // Map backend response to SDK objects
        let meeting = Meeting(
            externalMeetingId: connectionData.Meeting.MeetingId,
            mediaPlacement: MediaPlacement(
                audioFallbackUrl: connectionData.MediaPlacement.AudioFallbackUrl,
                audioHostUrl: connectionData.MediaPlacement.AudioHostUrl,
                signalingUrl: connectionData.MediaPlacement.SignalingUrl,
                turnControlUrl: connectionData.MediaPlacement.TurnControlUrl
            ),
            meetingId: connectionData.Meeting.MeetingId
        )
        
        let attendee = Attendee(
            attendeeId: connectionData.Attendee.AttendeeId,
            externalUserId: connectionData.Attendee.AttendeeId,
            joinToken: connectionData.Attendee.JoinToken
        )
        
        let config = MeetingSessionConfiguration(
            createMeetingResponse: CreateMeetingResponse(meeting: meeting),
            createAttendeeResponse: CreateAttendeeResponse(attendee: attendee)
        )
        
        self.meetingSession = DefaultMeetingSession(configuration: config, logger: logger)
        self.meetingSession?.audioVideo.addAudioVideoObserver(observer: self)
        
        // 2. Start Audio & Video
        try? self.meetingSession?.audioVideo.start()
        self.meetingSession?.audioVideo.startLocalVideo()
    }
    
    // 3. Bind Video to UI View
    func bindLocalVideo(to videoTile: VideoTile) {
        // 'localVideoView' is a DefaultVideoRenderView in your Storyboard/SwiftUI
        videoTile.bind(videoRenderView: self.localVideoView)
    }
}
```

---

## 3. The Agent Experience

How does the Agent see the customer?

1.  **Configuration**: In Amazon Connect configuration, enable **"Video calling"** in the "Contact flows" settings.
2.  **The CCP (Contact Control Panel)**:
    *   When the call arrives, the Agent accepts the voice call as usual.
    *   A **Video Window** automatically pops up (or is embedded) within the Agent Workspace.
    *   The Agent sees the Customer's camera feed.
    *   The Agent can toggle their own camera on/off to be seen by the customer.

**Agent Screen Share**:
*   The Agent clicks the **"Screen Share"** icon in the CCP.
*   The browser prompts the Agent to select **Specific Tab**, **Window**, or **Entire Screen**.
*   *Best Practice*: Agents should only share specific Windows (e.g., the Mortgage Calculator app) rather than the full screen to prevent showing other customer data inadvertently.

---

## 4. Customer Screen Sharing (iOS)

To allow the customer to show their screen (e.g., "I'm having trouble with this transaction error"):

### Step A: ReplayKit Setup
iOS screen sharing requires a **Broadcast Upload Extension**.

1.  **Xcode**: File > New > Target > **Broadcast Upload Extension**.
2.  **Code**: Use `AmazonChimeSDK` inside the extension to send sample buffers to the meeting.

### Step B: In-App Trigger
```swift
import ReplayKit

func startScreenShare() {
    let broadcastPicker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 50, height: 50))
    broadcastPicker.preferredExtension = "com.yourbank.app.broadcastExtension"
    broadcastPicker.showsMicrophoneButton = false
    
    // Simulate tap to launch picker
    for subview in broadcastPicker.subviews {
        if let button = subview as? UIButton {
            button.sendActions(for: .allTouchEvents)
        }
    }
}
```

---

## 5. PII Redaction (Securing Screen Share)

When a customer shares their screen, notifications or sensitive banking balances might be visible. We must **Redact** (mask) this data *before* it leaves the device.

### Technique A: Native Field Protection
iOS has built-in protection for password fields.
*   **Implementation**: Use `UITextField` with `isSecureTextEntry = true`.
*   **Result**: When screen sharing, iOS automatically renders this field as **Black/Hidden** to the remote viewer.

### Technique B: Custom View Masking (The "Security Curtain")
For non-password fields (like Account Balance or Transaction History), we must implement custom logic to hide them when capturing begins.

1.  **Detect Capture Status**:
    Listen for `UIScreen.capturedDidChangeNotification`.

    ```swift
    NotificationCenter.default.addObserver(
        self,
        selector: #selector(screenCaptureChanged),
        name: UIScreen.capturedDidChangeNotification,
        object: nil
    )
    
    @objc func screenCaptureChanged() {
        if UIScreen.main.isCaptured {
            // Screen Share Started -> Hide Sensitive Data
            accountBalanceLabel.alpha = 0
            overlayView.isHidden = false // Show a "Confidential" block
        } else {
            // Screen Share Stopped -> Restore
            accountBalanceLabel.alpha = 1
            overlayView.isHidden = true
        }
    }
    ```

### Technique C: Partial Sharing (Agent Side)
If the Agent shares their screen:
*   **WebRTC Constraint**: Browsers allow sharing specific **Tabs** or **Windows**.
*   **Enforcement**: Train agents to share *only* the specific "Co-Browsing" tab.
*   **Tech Control**: If building a custom Agent Desktop, use the `getDisplayMedia` API and filter the sources to exclude the main CRM window containing other customer data.

## 6. Summary Sequence

1.  **Customer**: Taps "Video Call" in iOS App.
2.  **App**: Joins Chime Meeting (Camera On).
3.  **Agent**: Accepts call in Connect CCP. Video Window opens.
4.  **Customer**: Taps "Share Screen".
5.  **iOS App**:
    *   Detects `isCaptured = true`.
    *   **Hides** `lblBalance` and `lblCreditCardNumber`.
    *   Starts ReplayKit stream.
6.  **Agent**: Sees Customer's screen, but sensitive fields appear blank/blurred.
