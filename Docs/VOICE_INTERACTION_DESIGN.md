# Amazon Connect Voice Interaction Testing Architecture

This document describes how to extend the automation framework to support **interactive voice testing**—simulating a user speaking to the bot and verifying the response.

## 1. The Challenge
Standard API tests (like `StartOutboundVoiceContact`) only initiate a call. They do not "speak" or "hear." To test natural language understanding (NLU) and IVR navigation, the test script must act as a **Virtual Customer**.

## 2. Test Data Structure
We extend `test_cases.json` to include voice interactions:

```json
{
  "name": "Account Balance Check",
  "input_speech": "I would like to check my account balance",
  "expected_queue": "AccountsQueue",
  "expected_voice_prompt": "Transferring you to the accounts department"
}
```

## 3. Workflow: How "Text-to-Voice" Works

The script performs the following steps to simulate the "I would like to check my account balance" scenario:

### Step A: Audio Generation (The "Mouth")
1.  **Read Input**: The script reads `"I would like to check my account balance"` from the JSON.
2.  **Synthesize**: It calls **AWS Polly** (or uses a local TTS engine) to convert this text into an audio file (MP3/PCM).
    ```python
    polly_client.synthesize_speech(Text="I would like to check my account balance", OutputFormat='mp3', VoiceId='Joanna')
    ```

### Step B: Call Injection (The "Phone")
To play this audio into the call, the script cannot use standard boto3. It must use a **Programmable Telephony Provider** (like Twilio or Amazon Chime SDK).

1.  **Dialing**: The script instructs the Telephony Provider to call the Amazon Connect instance.
2.  **Streaming**: Once connected, the script streams the audio generated in Step A into the call.
    *   *Result*: Amazon Connect "hears" the user speak the phrase.

### Step C: Flow Processing (Amazon Connect)
1.  **Capture**: Connect captures the audio.
2.  **NLU (Lex)**: It sends the audio to Amazon Lex.
3.  **Intent Recognition**: Lex identifies the `CheckBalance` intent.
4.  **Routing**: The Connect Flow logic routes the contact to the `AccountsQueue`.

## 4. Verification: How Success is Determined

There are two ways the script verifies if the flow was followed correctly:

### Method 1: Acoustic Verification (The "Ears")
*Use this to verify what the customer hears.*

1.  **Record**: The Telephony Provider records the audio coming *back* from Amazon Connect.
2.  **Transcribe**: The script sends this audio to **AWS Transcribe**.
3.  **Match**: It compares the transcribed text against `expected_voice_prompt`.
    *   *Check*: Does the transcript contain "Transferring you to the accounts department"?
    *   *Result*: **PASS** if matched.

### Method 2: Logical Verification (The "Logs")
*Use this to verify internal routing logic.*

1.  **Wait**: The script waits for the call to end (or poll periodically).
2.  **Query CTR**: The script calls `get_contact_attributes` or queries **Contact Trace Records (CTR)**.
3.  **Validate Queue**: It checks the `Queue` or `SystemEndpoint` attribute in the CTR.
    *   *Check*: Is `Queue.Name` == `AccountsQueue`?
    *   *Result*: **PASS** if the contact ended up in the right queue.

## 5. Summary of Architecture

| Component | Function | Technology |
| :--- | :--- | :--- |
| **Test Script** | Orchestrator | Python, pytest |
| **TTS Engine** | "Speaks" input text | AWS Polly |
| **Telephony Driver** | Connects call & pipes audio | Amazon Chime SDK / Twilio |
| **STT Engine** | "Listens" to response | AWS Transcribe |
| **Amazon Connect** | System Under Test | Connect Flows, Lex |

### Example Execution Log

```text
[TEST] Account Balance Check
1. GENERATING AUDIO: "I would like to check my account balance" (AWS Polly) -> OK
2. DIALING: +1-555-0199 via Twilio -> CONNECTED
3. PLAYING AUDIO: Stream sent -> COMPLETE
4. LISTENING: Recording response...
5. TRANSCRIBING: "Sure, let me get you to the accounts team." (AWS Transcribe)
6. VERIFYING:
   - Voice Match: "accounts team" found? YES
   - Queue Match: Contact routed to "AccountsQueue"? YES (Checked via API)
RESULT: PASS
```
