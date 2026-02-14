# Building Video Calling in Flutter (Mobile & Web) with AWS Chime SDK

This document provides a comprehensive, step-by-step guide to building a video calling application using Flutter (supporting iOS, Android, and Web) and the AWS Chime SDK.

## 1. Architecture Overview

To enable video calling, you need three main components:

1.  **AWS Backend (Serverless)**: Manages meeting sessions and authentication.
    *   **Lambda**: Creates meetings and attendees.
    *   **API Gateway**: Exposes the Lambda functions to your Flutter app.
    *   **DynamoDB (Optional)**: Stores active meeting IDs for joining.
2.  **Flutter Client (Frontend)**: The user interface.
    *   Uses a Platform Channel (Method Channel) to communicate with native Android (Kotlin) and iOS (Swift) Chime SDKs.
    *   *Note*: As of 2026, there is no official pure Dart SDK for Chime. We must bridge to the native SDKs.
3.  **Amazon Chime SDK**: The underlying real-time media service.

---

## 2. AWS Backend Setup (Server Side)

You need an API that your mobile app can call to say "Start a Meeting" or "Join a Meeting".

### Step A: Create the Lambda Function
This function will handle the `CreateMeeting` and `CreateAttendee` API calls.

1.  **Go to AWS Lambda** -> **Create Function** (`ChimeVideoBackend`).
2.  **Runtime**: Node.js 18.x or Python 3.9.
3.  **Permissions**: Attach a policy allowing `chime:CreateMeeting` and `chime:CreateAttendee`.
4.  **Code (Node.js Example)**:
    ```javascript
    const AWS = require('aws-sdk');
    const chime = new AWS.ChimeSDKMeetings({ region: 'us-east-1' });

    exports.handler = async (event) => {
        const body = JSON.parse(event.body);
        const action = body.action; // 'create' or 'join'
        
        if (action === 'create') {
            // 1. Create Meeting
            const meetingResponse = await chime.createMeeting({
                ClientRequestToken:  require('crypto').randomUUID(),
                MediaRegion: 'us-east-1'
            }).promise();
            
            // 2. Create Attendee (Host)
            const attendeeResponse = await chime.createAttendee({
                MeetingId: meetingResponse.Meeting.MeetingId,
                ExternalUserId: body.userId // e.g. 'user-1'
            }).promise();
            
            return {
                statusCode: 200,
                body: JSON.stringify({
                    meeting: meetingResponse.Meeting,
                    attendee: attendeeResponse.Attendee
                })
            };
        } 
        
        if (action === 'join') {
            // Join existing meeting
            const attendeeResponse = await chime.createAttendee({
                MeetingId: body.meetingId,
                ExternalUserId: body.userId
            }).promise();
            
            return {
                statusCode: 200,
                body: JSON.stringify({
                    meeting: null, // Client should already have this or fetch it
                    attendee: attendeeResponse.Attendee
                })
            };
        }
    };
    ```

### Step B: Setup API Gateway
1.  **Create REST API**: "ChimeVideoAPI".
2.  **Create Resource**: `/meeting`.
3.  **Create Method**: `POST` -> Integration type "Lambda Function" -> Select `ChimeVideoBackend`.
4.  **Deploy API**: Create a Stage (e.g., `prod`).
5.  **Copy URL**: e.g., `https://xyz.execute-api.us-east-1.amazonaws.com/prod/meeting`.

---

## 3. Flutter Client Setup (Mobile Side)

Since there is no official pure Flutter SDK, you will use a **MethodChannel** to talk to native code (Swift/Kotlin) that runs the official Chime SDKs.

### Step A: Project Configuration
1.  **Create Flutter App**: `flutter create chime_video_app`
2.  **Add Dependencies**:
    *   `http`: For calling your API Gateway.
    *   `permission_handler`: For Camera/Mic permissions.

### Step B: iOS Setup (Native Bridge)
1.  **Podfile**: Add `pod 'AmazonChimeSDK'` to `ios/Podfile`.
2.  **Info.plist**: Add keys for `NSCameraUsageDescription` and `NSMicrophoneUsageDescription`.
3.  **Swift Implementation (`ios/Runner/AppDelegate.swift`)**:
    *   Implement a `FlutterMethodChannel`.
    *   On `join` method call, initialize `DefaultMeetingSession` using the JSON data passed from Flutter.
    *   Create a `VideoTileController` to render video.
    *   **View Factory**: You need to implement a `FlutterPlatformView` to render the video Native View inside a Flutter Widget.

### Step C: Android Setup (Native Bridge)
1.  **Gradle**: Add `implementation 'com.amazonaws:amazon-chime-sdk:0.18.0'` (check latest version).
2.  **Permissions**: Add `CAMERA`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS` to `AndroidManifest.xml`.
3.  **Kotlin Implementation (`MainActivity.kt`)**:
    *   Similar to iOS, setup a `MethodChannel`.
    *   Parse the Meeting/Attendee JSON.
    *   Initialize `DefaultMeetingSession`.
    *   Pass the video texture back to Flutter or use a Platform View (`AndroidView`).

### Step D: Flutter Implementation (Dart)

**1. API Service**:
```dart
Future<Map<String, dynamic>> createMeeting(String userId) async {
  final response = await http.post(
    Uri.parse('YOUR_API_GATEWAY_URL/meeting'),
    body: jsonEncode({'action': 'create', 'userId': userId}),
  );
  return jsonDecode(response.body);
}
```

**2. Method Channel**:
```dart
static const platform = MethodChannel('com.example.chime/video');

Future<void> joinMeeting(Map<String, dynamic> meetingData) async {
  try {
    // Send the AWS response directly to native code
    await platform.invokeMethod('join', meetingData);
  } on PlatformException catch (e) {
    print("Failed to join: '${e.message}'.");
  }
}
```

**3. UI Widget**:
```dart
class VideoCallScreen extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Expanded(
          // For iOS
          child: UiKitView(viewType: 'chime_video_view'),
          // For Android use AndroidView(viewType: 'chime_video_view')
        ),
        ControlButtons(), // Mute, Hangup, etc.
      ],
    );
  }
}
```

## 4. Web Support (Flutter Web)

Flutter Web cannot use Method Channels to native SDKs. Instead, you must use **JavaScript Interop**.

1.  **Include JS SDK**: Add `<script src="https://unpkg.com/amazon-chime-sdk-js@3.0.0/build/amazon-chime-sdk.min.js"></script>` to your `index.html`.
2.  **JS Wrapper**: Write a small `chime_wrapper.js` file that exposes functions like `joinMeeting(meetingJson, attendeeJson)`.
3.  **Dart Interop**:
    ```dart
    import 'dart:js' as js;

    void joinWeb(Map meeting, Map attendee) {
      js.context.callMethod('joinMeeting', [jsonEncode(meeting), jsonEncode(attendee)]);
    }
    ```
4.  **Video Element**: Use `HtmlElementView` in Flutter to display the `<video>` tag created by the Chime JS SDK.

---

## 5. Sequence of Events

1.  **User A** opens app, clicks "Start Call".
2.  **Flutter** calls AWS API Gateway (`/meeting`).
3.  **Lambda** creates Meeting & Attendee in Chime, returns JSON.
4.  **Flutter** receives JSON and calls `MethodChannel.invokeMethod('join', json)`.
5.  **Native Code (Swift/Kotlin)**:
    *   Initializes `DefaultMeetingSession`.
    *   Binds Audio/Video.
    *   Starts the session.
6.  **Video**: Native code renders video to a texture/view, which Flutter displays via `UiKitView` / `AndroidView`.

## 6. Summary Checklist

- [ ] **AWS**: Lambda & API Gateway deployed.
- [ ] **iOS**: `Podfile` updated, Camera permissions added, `AppDelegate.swift` logic implemented.
- [ ] **Android**: `build.gradle` updated, Permissions added, `MainActivity.kt` logic implemented.
- [ ] **Flutter**: Method Channel logic and UI implementation.
- [ ] **Web**: JS Interop and `HtmlElementView` setup.
