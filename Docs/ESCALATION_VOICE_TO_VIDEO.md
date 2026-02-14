# Escalating Voice to Video: The "See-What-I-See" Pattern
**Date:** 2026-02-14
**Context:** Amazon Connect In-App Calling (WebRTC)

This document details the technical implementation of **Escalating** an active Click-to-Call voice session to a Video session with Screen Sharing. This pattern reduces initial friction (customer just wants to talk) while allowing capabilities to expand if complex support is needed.

## 1. The Core Concept
You do **not** need to hang up and call back.
The `StartWebRTCContact` API creates a session capable of Audio, Video, and Data. To "escalate", we simply **unmute** the video track on the client side and render it on the agent side.

## 2. Architecture & Prerequisites

### Architecture
*   **Session Persistence**: The Chime SDK meeting session ID remains the same throughout the upgrade.
*   **Bandwidth**: The SDK automatically adapts bitrate. Adding video increases bandwidth usage significantly.

### Connect Configuration
1.  **Enable Video**: In your Amazon Connect Instance settings, ensure "Video calling" is enabled.
2.  **Security Profile**: Ensure the Agent's Security Profile has "Video" permissions enabled.

---

## 3. Step-by-Step Implementation

### Step A: The Client Side (iOS/Android/Web)

**1. Initial State (Voice Only)**
The user is in a call. The local camera is OFF.
```swift
// Audio is started, but Video is not
meetingSession?.audioVideo.start() 
// NOT calling startLocalVideo() yet
```

**2. The Trigger (User Action or Agent Request)**
*   **Scenario 1: User Taps "Turn on Camera"**: The App requests permission and starts the stream.
*   **Scenario 2: Agent Requests Video**:
    *   Agent clicks "Video" in CCP.
    *   **This does NOT force the camera on** (Privacy).
    *   It sends a data message (or the Agent simply asks verbally: "Can you turn on your camera?").

**3. Enabling the Stream (Code)**
When the user agrees/taps the button:

```swift
func escalateToVideo() {
    // 1. Request Camera Permission (Just-in-Time)
    AVCaptureDevice.requestAccess(for: .video) { granted in
        if granted {
            // 2. Start the Local Video Tile
            self.meetingSession?.audioVideo.startLocalVideo()
            
            // 3. Update UI to show self-view
            DispatchQueue.main.async {
                self.showLocalVideoView()
            }
        }
    }
}
```

### Step B: The Agent Side (Amazon Connect CCP)

1.  **Automatic Detection**: The Amazon Connect CCP is smart. It listens for incoming video tracks.
2.  **UI Update**:
    *   The moment `startLocalVideo()` is called on the mobile app, the CCP automatically expands a **Video Window**.
    *   The Agent sees the customer immediately.
3.  **Agent Privacy**: The Agent's camera remains OFF until they explicitly click the "Camera" icon in their CCP to reciprocate.

---

## 4. Handling Screen Share Escalation

If the goal is "Show me the error on your screen" rather than "Show me your face":

**1. Client Code (iOS ReplayKit)**
Instead of `startLocalVideo()` (which uses the Camera), we start the **Content Share**.

```swift
func escalateToScreenShare() {
    // 1. Start ReplayKit Broadcast
    let broadcastController = RPSystemBroadcastPickerView(frame: .zero)
    // ... trigger broadcast picker ...
    
    // 2. In Broadcast Extension:
    // This sends the screen as a separate "Content" video track
    self.meetingSession?.audioVideo.startContentShare(source: screenSource)
}
```

**2. Agent Experience**
*   Connect CCP distinguishes between "Webcam Video" and "Content Share".
*   The screen share appears in a larger, dedicated viewing area (often maximizing the video window) for better readability.

---

## 5. Sequence Diagram: The Escalation Flow

```text
[Customer (App)]                  [Amazon Connect]                  [Agent (CCP)]
       |                                 |                                |
       | (1) Audio Call Active           |                                |
       |<========================= (WebRTC Audio) =======================>|
       |                                 |                                |
       |                  (2) Verbal: "Can I see the error?"              |
       |<-----------------------------------------------------------------|
       |                                 |                                |
       | (3) User Taps "Video"           |                                |
       |     & Grants Permission         |                                |
       |                                 |                                |
       | (4) startLocalVideo()           |                                |
       |-------------------------------->|                                |
       |                                 | (5) Track Added Event          |
       |                                 |------------------------------->|
       |                                 |                                |
       |                                 | (6) CCP Opens Video Window     |
       |                                 |     (Customer Face Visible)    |
       |                                 |                                |
       | (7) Two-Way Video (Optional)    |                                |
       |<========================= (WebRTC Video) =======================>|
```

## 6. Managing State & Privacy

### "Mute" vs. "Stop"
*   **Video Mute (Stop)**: Call `meetingSession.audioVideo.stopLocalVideo()`. The track is removed. The Agent's video window closes or goes black.
*   **Audio Mute**: `realtimeLocalMute()`. Video continues.

### Backgrounding (Mobile Specific)
*   **Behavior**: When the user puts the banking app in the background (e.g., to find a document), iOS **pauses** the camera access.
*   **Handling**:
    *   The video track will freeze or go black for the Agent.
    *   **Best Practice**: Listen for `UIApplication.willResignActiveNotification` and explicitly stop the local video to save bandwidth/battery, sending a data message "User paused video".

## 7. Summary
Escalation is **client-driven**. The session established by `StartWebRTCContact` is ready for video from millisecond zero. You simply need to:
1.  **Request Permissions** (Camera/Screen).
2.  **Start the Track** (`startLocalVideo`).
3.  **Handle the UI** (Show self-view).

Connect handles the Agent UI automatically.
