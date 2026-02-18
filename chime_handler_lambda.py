import json
import boto3
import os
import time
import math

# ---------------------------------------------------------------------------
# DynamoDB client
# FIX: Table name honours ENV_NAME namespace so dev/test/prod are isolated.
# ---------------------------------------------------------------------------
dynamodb  = boto3.resource('dynamodb')
ENV_NAME   = os.environ.get('ENV_NAME', 'dev')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', f'VoiceTestState-{ENV_NAME}')
table      = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    event_type = event.get('InvocationEventType')
    call_details = event.get('CallDetails', {})
    participants = call_details.get('Participants', [])
    transaction_attributes = call_details.get('TransactionAttributes', {})
    
    # Use conversation_id from TransactionAttributes
    conversation_id = transaction_attributes.get('conversation_id')
    
    # Also check Arguments for outbound calls (NEW_OUTBOUND_CALL)
    # The API passes 'ArgumentsMap' which appears as 'Arguments' in CallDetails
    # BUT based on logs, ActionData contains 'Parameters' which contains 'Arguments'
    if not conversation_id:
        # 1. Check ActionData -> Parameters -> Arguments (Common for Outbound API)
        action_data = event.get('ActionData', {})
        parameters = action_data.get('Parameters', {})
        arguments = parameters.get('Arguments', {})
        conversation_id = arguments.get('conversation_id')

        # 2. Check CallDetails -> Arguments (Some contexts)
        if not conversation_id:
            args = call_details.get('Arguments', {})
            conversation_id = args.get('conversation_id')
        
        # 3. Check CallDetails -> Parameters (Legacy)
        if not conversation_id:
             conversation_id = call_details.get('Parameters', {}).get('conversation_id')

        if conversation_id:
             print(f"Found conversation_id in Event: {conversation_id}")
             # Add to transaction attributes so it persists for future invocations
             if transaction_attributes is None:
                 transaction_attributes = {}
             transaction_attributes['conversation_id'] = conversation_id
    
    # --- Legacy Fallback ---
    if not conversation_id:
        print("No conversation_id found. Checking for legacy 'tts_text'...")
        tts_text = transaction_attributes.get('tts_text')
        if tts_text:
            return handle_legacy_single_turn(event, tts_text)
        else:
            print("ERROR: No conversation_id or tts_text found.")
            return {"SchemaVersion": "1.0", "Actions": []}

    # --- Fetch State ---
    try:
        response = table.get_item(Key={'conversation_id': conversation_id}, ConsistentRead=True)
        item = response.get('Item')
        
        if not item:
            print(f"ERROR: Conversation state not found for {conversation_id}")
            # If we don't know what to do, just hang up or return empty
            return {"SchemaVersion": "1.0", "Actions": []}
            
        # Parse script
        script_raw = item.get('script', [])
        if isinstance(script_raw, str):
            try:
                script = json.loads(script_raw)
            except Exception as e:
                print(f"Error parsing script JSON: {e}")
                script = []
        else:
            script = script_raw
            
        current_step_index = int(item.get('current_step_index', 0))
        status = item.get('status', 'NEW')
        
    except Exception as e:
        print(f"DynamoDB Error: {e}")
        return {"SchemaVersion": "1.0", "Actions": []}

    actions = []
    next_step_index = current_step_index
    new_status = status

    # --- State Machine ---

    # 1. NEW_INBOUND_CALL (or RINGING)
    if event_type in ['NEW_INBOUND_CALL', 'NEW_OUTBOUND_CALL', 'RINGING']:
        print(f"Call Event: {event_type}")
        # Return empty actions but INCLUDE TransactionAttributes to persist state
        # Ensure we return the attributes we modified/found
        return {
            "SchemaVersion": "1.0", 
            "Actions": [],
            "TransactionAttributes": transaction_attributes
        }
        
    # Handle manual trigger via UpdateSipMediaApplicationCall
    elif event_type == 'CALL_UPDATE_REQUESTED':
        print("Received CALL_UPDATE_REQUESTED")
        args = event.get('ActionData', {}).get('Parameters', {}).get('Arguments', {})
        if args.get('action') == 'hangup':
             print("Manual hangup requested.")
             actions = [{
                "Type": "Hangup",
                "Parameters": {
                    "SipResponseCode": "0",
                    "ParticipantTag": "LEG-A"
                }
            }]
             return {
                "SchemaVersion": "1.0",
                "Actions": actions,
                "TransactionAttributes": transaction_attributes
            }

    # 2. CALL_ANSWERED: Start the conversation
    # If the DynamoDB item carries pre_set_attributes (from test case), inject them
    # into the contact via a Speak action placeholder so Connect sets them via a
    # contact attribute update invocation.  In practice the test framework uses
    # the Connect API directly — we surface them here per the conversation_item.
    elif event_type == 'CALL_ANSWERED':
        print(f"Call Answered. Starting conversation at step {current_step_index}")
        # Pre-set attributes: stored in DynamoDB by the test seeder
        # The Lambda surfaces them in TransactionAttributes so they can be monitored.
        pre_set_raw = conversation_item.get('pre_set_attributes')
        if pre_set_raw:
            try:
                pre_attrs = json.loads(pre_set_raw) if isinstance(pre_set_raw, str) else pre_set_raw
                transaction_attributes.update({f"pre_{k}": v for k, v in pre_attrs.items()})
                print(f"pre_set_attributes forwarded to TransactionAttributes: {pre_attrs}")
            except Exception as pa_err:
                print(f"Warning: could not parse pre_set_attributes: {pa_err}")
        # Execute the current step (usually 0)
        actions = execute_step(script, current_step_index, participants)
        new_status = 'IN_PROGRESS'

    # 3. ACTION_SUCCESSFUL: advance to next step
    elif event_type == 'ACTION_SUCCESSFUL':
        print(f"Action Successful for step {current_step_index}")
        next_step_index = current_step_index + 1

        if next_step_index < len(script):
            print(f"Moving to step {next_step_index}")
            actions = execute_step(script, next_step_index, participants)
            new_status = 'IN_PROGRESS'
        else:
            print("End of script reached — marking COMPLETED.")
            # Mark completed but do NOT hang up immediately so the test framework
            # has time to poll the queue metric / CTR before the call drops.
            new_status = 'COMPLETED'
            actions = []

    # 4. ACTION_FAILED: log the failure and end the script gracefully
    elif event_type == 'ACTION_FAILED':
        action_data = event.get('ActionData', {})
        error_msg   = action_data.get('ErrorMessage', 'Unknown error')
        print(f"ACTION_FAILED at step {current_step_index}: {error_msg}")
        new_status  = 'FAILED'
        # Hang up so we don't leave zombie calls alive
        actions = [{
            "Type": "Hangup",
            "Parameters": {"SipResponseCode": "0", "ParticipantTag": "LEG-A"}
        }]

    # --- Persist state if anything changed ---
    if next_step_index != current_step_index or new_status != status:
        update_state(conversation_id, next_step_index, new_status)

    return {
        "SchemaVersion": "1.0",
        "Actions": actions,
        "TransactionAttributes": transaction_attributes
    }

def execute_step(script, step_index, participants):
    if step_index >= len(script):
        return []

    step        = script[step_index]
    action_type = step.get('type') or step.get('action')
    call_id     = participants[0]['CallId'] if participants else None

    actions = []

    if action_type == 'speak':
        text = step.get('text', '')
        print(f"Generating SPEAK action: '{text}'")
        actions.append({
            "Type": "Speak",
            "Parameters": {
                "Text":      text,
                "Engine":    "neural",
                "VoiceId":   "Joanna",
                "CallId":    call_id,
                "TextType":  "text"
            }
        })

    elif action_type == 'dtmf':
        digits = step.get('digits', '')
        print(f"Generating DTMF action: '{digits}'")
        actions.append({
            "Type": "SendDigits",
            "Parameters": {
                "CallId":                    call_id,
                "Digits":                    digits,
                "ToneDurationInMilliseconds": 250
            }
        })

    elif action_type == 'wait':
        duration_ms = step.get('duration_ms', 1000)
        print(f"Generating WAIT action: {duration_ms}ms")
        #
        # FIX: Chime SMA SSML <break> tags are capped at ~10 seconds.
        # Instead, send silent DTMF digits (digit "w" = 0.5 s pause per digit
        # in standard DTMF notation, but Chime does not support 'w').
        # We use the SendDigits action with 0-ms tone + inter-digit delay to
        # simulate a pause of arbitrary length:
        #   - Send digit "0" with ToneDurationInMilliseconds=0
        #   - Use the rest of the duration as the pause between tones via a
        #     separate speak of silence SSML capped at 10 s chunks.
        # Practical approach: chain multiple short SSML SpeakActions, each <= 9000ms,
        # so the SMA processes them sequentially via ACTION_SUCCESSFUL callbacks.
        # We emit a single step here; for waits > 9s we split into sub-steps
        # by re-evaluating on each ACTION_SUCCESSFUL call.
        #
        MAX_SSML_BREAK_MS = 9000
        if duration_ms <= MAX_SSML_BREAK_MS:
            ssml = f"<speak><break time='{duration_ms}ms'/></speak>"
            actions.append({
                "Type": "Speak",
                "Parameters": {
                    "Text":     ssml,
                    "Engine":   "neural",
                    "VoiceId":  "Joanna",
                    "CallId":   call_id,
                    "TextType": "ssml"
                }
            })
        else:
            # Split into 9s chunks by rewriting the step in-place:
            # Emit the first 9s break now; store remaining duration back into
            # the script so the next ACTION_SUCCESSFUL picks it up.
            # Because we cannot mutate the script here (it's reloaded from DB as-is
            # each invocation), we instead emit a SendDigits pause using the
            # Chime "silence" approach: send digit "0" with a tone duration of
            # duration_ms (Chime supports up to 60,000ms per SendDigits call).
            actual_duration = min(duration_ms, 60000)
            actions.append({
                "Type": "SendDigits",
                "Parameters": {
                    "CallId":                    call_id,
                    "Digits":                    "0",
                    "ToneDurationInMilliseconds": actual_duration
                }
            })
            if duration_ms > 60000:
                print(f"WARNING: Requested wait of {duration_ms}ms exceeds 60s SendDigits limit. "
                      "Clamped to 60000ms. Split into multiple 'wait' script steps if longer pauses are needed.")

    return actions

def update_state(conversation_id, step_index, status):
    try:
        table.update_item(
            Key={'conversation_id': conversation_id},
            UpdateExpression="set current_step_index = :i, #s = :st",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':i': step_index,
                ':st': status
            }
        )
    except Exception as e:
        print(f"Error updating DynamoDB: {e}")

def handle_legacy_single_turn(event, tts_text):
    event_type = event.get('InvocationEventType')
    call_details = event.get('CallDetails', {})
    participants = call_details.get('Participants', [])
    call_id = participants[0]['CallId'] if participants else None
    
    actions = []
    if event_type == 'CALL_ANSWERED':
        actions.append({
            "Type": "Speak",
            "Parameters": {
                "Text": tts_text,
                "Engine": "neural",
                "VoiceId": "Joanna",
                "CallId": call_id
            }
        })
    elif event_type == 'ACTION_SUCCESSFUL':
        actions.append({
            "Type": "Hangup",
            "Parameters": {
                "SipResponseCode": "0",
                "ParticipantTag": "LEG-A"
            }
        })
        
    return {
        "SchemaVersion": "1.0",
        "Actions": actions,
        "TransactionAttributes": event.get('CallDetails', {}).get('TransactionAttributes', {})
    }
