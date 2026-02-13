# AWS Cloud WebRTC Infrastructure Setup

You asked about the **WebRTC setup** required on AWS Cloud to support the Chime SDK Flutter implementation.

## The Short Answer
**You do NOT need to set up your own WebRTC servers** (like STUN, TURN, or SFU instances) on EC2.

The **Amazon Chime SDK** is a fully managed WebRTC service. When you call `chime.createMeeting()`, AWS spins up a slice of their global WebRTC infrastructure for you instantly.

However, you **DO** need to configure the following components on AWS to make the WebRTC connection work effectively.

---

## 1. The Signaling Layer (Your Responsibility)
WebRTC requires a "Signaling Channel" to exchange session data (SDP offer/answer, candidates) before the media flows. Amazon Chime SDK simplifies this:

*   **Standard WebRTC**: You usually need a WebSocket server to exchange SDP blobs.
*   **Chime SDK WebRTC**: AWS handles the SDP negotiation internally via the SDK.
    *   **Your Job**: You only need to securely transport the `Meeting` and `Attendee` JSON objects from your backend (Lambda) to the mobile app.
    *   **Setup**: Use **API Gateway (REST)** or **AppSync (GraphQL)**.

**Architecture:**
```text
[Mobile App] --(HTTPS)--> [API Gateway] --(Lambda)--> [Amazon Chime Control Plane]
```

## 2. Media Region Selection (Latency Optimization)
WebRTC is sensitive to latency. You must tell AWS *where* to host the media session.

*   **Setup**: In your Lambda `createMeeting` call, you must specify `MediaRegion`.
*   **Best Practice**:
    *   Detect the user's location (e.g., from CloudFront headers or IP).
    *   Pick the closest available Chime Region (e.g., `us-east-1`, `eu-central-1`, `ap-northeast-1`).
    *   *Note*: This determines where the WebRTC "Media Bridge" (SFU) lives.

## 3. Network & Firewall Configuration (UDP/TCP)
Even though AWS manages the servers, your clients (Mobile Apps) and your corporate firewalls must allow the traffic.

*   **Protocol**: WebRTC prefers **UDP** for video/audio.
*   **Ports**:
    *   **UDP 3478**: STUN/TURN (Media).
    *   **TCP 443**: Signaling & Fallback Media (TLS).
*   **IP Ranges**: If your app runs on a restricted corporate Wi-Fi, you may need to whitelist the Amazon Chime SDK IP ranges (`Chime` service in `ip-ranges.json`).

## 4. Media Pipelines (Advanced WebRTC Features)
If you need to do more than just "talk", you need **Amazon Chime SDK Media Pipelines**.

*   **Recording**: To record the WebRTC stream to S3.
*   **Streaming**: To broadcast the WebRTC session to RTMP (YouTube/Twitch) or Amazon IVS.
*   **Voice Analytics**: To feed the audio into Amazon Transcribe Call Analytics.

**Setup**:
1.  Create a **Media Pipeline** in the Chime Console.
2.  Configure an S3 bucket for artifacts.
3.  Trigger the pipeline via API (`createMediaLiveConnectorPipeline`) when the meeting starts.

## 5. Scalability & Quotas
*   **Service Quotas**: Check your AWS limits.
    *   Default is usually 250 attendees per meeting.
    *   Maximum 25 video tiles active at once.
*   **Scaling**: AWS handles the autoscaling of the WebRTC media nodes. You don't need to configure Auto Scaling Groups.

---

## Summary of AWS Cloud Setup

| Component | standard WebRTC Setup | AWS Chime SDK Setup |
| :--- | :--- | :--- |
| **Media Server (SFU)** | Deploy Kurento/Jitsi on EC2 | **Managed (No Setup)** |
| **NAT Traversal** | Deploy STUN/TURN (Coturn) | **Managed (No Setup)** |
| **Signaling** | Build WebSocket Server | **Build Simple REST API (Lambda)** |
| **Scaling** | Config ASG / Load Balancers | **Managed (Automatic)** |
| **Security** | Manage Certs / DTLS | **Managed (AWS Shield)** |

## Implementation Checklist
1.  [ ] **Lambda**: Ensure `chime:CreateMeeting` permission.
2.  [ ] **Latency**: Logic to pick the nearest `MediaRegion`.
3.  [ ] **Client**: Ensure app has internet access to AWS IP ranges.
4.  [ ] **Pipelines**: (Optional) Configure S3 bucket if recording is needed.
