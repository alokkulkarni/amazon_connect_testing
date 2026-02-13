# Real-Time Sentiment & Summarization with SLMs on Amazon Connect

This document details the architecture and step-by-step setup to integrate **Small Language Models (SLMs)** via Amazon Bedrock into Amazon Connect for:
1.  **Real-Time Sentiment Analysis**: Detecting angry customers instantly to trigger escalations.
2.  **Post-Call Summarization**: Generating concise notes automatically using cost-effective models.

---

## 1. Architecture Overview

### High-Level Data Flow

```text
[Customer] <---> [Amazon Connect]
                      |
                      | (Media Stream)
                      v
             [Kinesis Video Stream]
                      |
                      v
           [Lambda: Audio Processor] <--- (Transcribe Stream)
                      |
                      v
             [Kinesis Data Stream] ----> [Lambda: Inference Orchestrator]
                                                      |
                                       +--------------+--------------+
                                       |                             |
                               (Real-Time Path)               (Post-Call Path)
                                       |                             |
                                [Bedrock: Titan Lite]        [Bedrock: Llama 3 8B]
                                  (Sentiment)                   (Summarization)
                                       |                             |
                                       v                             v
                                [Connect API]                 [S3 / CRM]
                           (Update Contact Attributes)      (Save Call Notes)
```

### Component Selection
*   **Voice Source**: Amazon Connect.
*   **Streaming**: Amazon Kinesis Video Streams (KVS) for raw audio, Kinesis Data Streams (KDS) for text segments.
*   **Transcription**: Amazon Transcribe (Standard or Medical).
*   **Orchestration**: AWS Lambda (Python).
*   **AI Model (Sentiment)**: **Amazon Titan Text Lite** (Fast, cheap classification).
*   **AI Model (Summary)**: **Meta Llama 3 8B Instruct** (High quality reasoning, low cost).

---

## 2. Prerequisites
1.  **AWS Account** with Admin access.
2.  **Amazon Bedrock Access**:
    *   Go to **AWS Console > Bedrock > Model access**.
    *   Request access for **Amazon Titan Text Lite** and **Meta Llama 3**.
3.  **Amazon Connect Instance** created and configured.

---

## 3. Step-by-Step Implementation

### Phase 1: Enable Streaming in Amazon Connect
To get the audio out of Connect in real-time, we enable "Live Media Streaming".

1.  Log in to **AWS Console > Amazon Connect**.
2.  Select your instance -> **Data streaming**.
3.  Check **Enable live media streaming**.
4.  **Prefix**: `connect-audio-`.
5.  **Encryption**: Select a KMS key.
6.  **retention**: 24 hours.
7.  Click **Save**.

### Phase 2: Create the Transcription Lambda (Audio -> Text)
Connect streams *Audio*, but SLMs need *Text*. We need a processor to transcribe.

1.  **Create Lambda**: `ConnectTranscriber`.
2.  **Runtime**: Python 3.9+.
3.  **Permissions**:
    *   `kinesisvideo:GetDataEndpoint`, `kinesisvideo:GetMedia`.
    *   `transcribe:StartStreamTranscription`.
    *   `kinesis:PutRecord` (to send text to the next stage).
4.  **Code Logic**:
    *   Triggered by the Connect Contact Flow (via "Start Media Streaming" block).
    *   Consumes bytes from KVS.
    *   Sends bytes to **Amazon Transcribe Streaming API**.
    *   Receives text chunks.
    *   Puts text chunks into a **Kinesis Data Stream** (e.g., `LiveTranscriptStream`).

### Phase 3: The Inference Orchestrator (Text -> SLM)
This is the "Brain" that decides when to call the AI.

1.  **Create Kinesis Data Stream**: `LiveTranscriptStream`.
2.  **Create Lambda**: `SLMInferenceEngine`.
3.  **Trigger**: Kinesis Data Stream (`LiveTranscriptStream`).
4.  **Permissions**: `bedrock:InvokeModel`, `connect:UpdateContactAttributes`.

**Code Implementation (Python)**:

```python
import boto3
import json

bedrock = boto3.client('bedrock-runtime')
connect = boto3.client('connect')

def lambda_handler(event, context):
    for record in event['Records']:
        # 1. Parse Transcript Segment
        payload = json.loads(record['kinesis']['data'])
        transcript_text = payload['transcript']
        contact_id = payload['contactId']
        is_final = payload['isPartial'] == False

        # --- REAL-TIME SENTIMENT (Run on every final sentence) ---
        if is_final:
            sentiment = get_sentiment_titan(transcript_text)
            print(f"Detected Sentiment: {sentiment}")
            
            if sentiment == 'NEGATIVE':
                # Trigger Escalation in Connect
                connect.update_contact_attributes(
                    InstanceId='your-instance-id',
                    InitialContactId=contact_id,
                    Attributes={'RealTimeSentiment': 'NEGATIVE'}
                )

        # --- POST-CALL SUMMARIZATION (Accumulate or run at end) ---
        # In a real app, you might aggregate text in DynamoDB and run this 
        # only when the call ends or reaches a duration threshold.
        # summary = get_summary_llama(full_conversation_text)

def get_sentiment_titan(text):
    # Call Amazon Titan Text Lite
    prompt = f"Classify the sentiment of this customer statement as POSITIVE, NEUTRAL, or NEGATIVE. Text: {text}"
    
    body = json.dumps({
        "inputText": prompt,
        "textGenerationConfig": { "maxTokenCount": 10, "temperature": 0 }
    })
    
    response = bedrock.invoke_model(
        modelId='amazon.titan-text-lite-v1',
        contentType='application/json',
        accept='application/json',
        body=body
    )
    
    response_body = json.loads(response['body'].read())
    return response_body['results'][0]['outputText'].strip()

def get_summary_llama(conversation_text):
    # Call Meta Llama 3 8B
    prompt = f"""
    <|begin_of_text|><|start_header_id|>user<|end_header_id|>
    Summarize the following call in 3 bullet points:
    {conversation_text}
    <|eot_id|><|start_header_id|>assistant<|end_header_id|>
    """
    
    # ... Bedrock invoke code for Llama 3 ...
```

### Phase 4: Configure Contact Flow
Now, wire it up in the visual editor.

1.  **Open Connect Flow Designer**.
2.  Add **"Start media streaming"** block immediately after the call starts.
    *   Select "From Customer" and "From Agent".
3.  Add **"Invoke AWS Lambda function"** block.
    *   Target: `ConnectTranscriber`.
    *   This starts the background process that listens to the stream.
4.  **Handle Sentiment Trigger**:
    *   You don't need a block for this. The Lambda updates the `RealTimeSentiment` attribute *asynchronously*.
    *   However, if you want the Agent to see it, use **Guides** or a custom **CCP overlay** that polls for this attribute change.

---

## 4. Cost Optimization (Why SLMs?)

| Task | Model | Cost (approx) | Why? |
| :--- | :--- | :--- | :--- |
| **Sentiment** | **Titan Text Lite** | ~$0.0003 / 1k tokens | extremely cheap for frequent, small checks. |
| **Summary** | **Llama 3 8B** | ~$0.0004 / 1k tokens | 10x cheaper than GPT-4/Claude Opus, sufficient for summaries. |

## 5. Deployment Checklist
1.  [ ] **Bedrock**: Models enabled (Titan Lite, Llama 3).
2.  [ ] **Connect**: Media Streaming enabled.
3.  [ ] **IAM**: Roles created for Transcriber (KVS access) and Inference (Bedrock access).
4.  [ ] **Lambda**: `ConnectTranscriber` deployed with KVS/Transcribe logic.
5.  [ ] **Lambda**: `SLMInferenceEngine` deployed with Bedrock logic.
6.  [ ] **Streams**: Kinesis Data Stream created.
