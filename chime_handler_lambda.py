import json
import boto3
import os
import time

# DynamoDB client
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'VoiceTestState')
table = dynamodb.Table(TABLE_NAME)

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
    elif event_type == 'CALL_ANSWERED':
        print(f"Call Answered. Starting conversation at step {current_step_index}")
        # Execute the current step (usually 0)
        actions = execute_step(script, current_step_index, participants)
        new_status = 'IN_PROGRESS'

    # 3. ACTION_SUCCESSFUL: Move to next step
    elif event_type == 'ACTION_SUCCESSFUL':
        print(f"Action Successful for step {current_step_index}")
        
        # Determine next step
        next_step_index = current_step_index + 1
        
        if next_step_index < len(script):
            print(f"Moving to step {next_step_index}")
            actions = execute_step(script, next_step_index, participants)
            new_status = 'IN_PROGRESS'
        else:
            print("End of script reached.")
            # If the script is done, we mark as completed but don't hang up immediately
            # to allow post-script validation (like agent answer).
            new_status = 'COMPLETED'
            actions = []
            
    # --- Update State ---
    # Only update if changed
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
    
    step = script[step_index]
    action_type = step.get('type')
    
    # Fallback to 'action' key if 'type' missing
    if not action_type:
        action_type = step.get('action')
        
    call_id = participants[0]['CallId'] if participants else None
    
    actions = []
    
    if action_type == 'speak':
        text = step.get('text', '')
        print(f"Generating SPEAK action: '{text}'")
        actions.append({
            "Type": "Speak",
            "Parameters": {
                "Text": text,
                "Engine": "neural",
                "VoiceId": "Joanna",
                "CallId": call_id,
                "TextType": "text"
            }
        })
        
    elif action_type == 'dtmf':
        digits = step.get('digits', '')
        print(f"Generating DTMF action: '{digits}'")
        actions.append({
            "Type": "SendDigits",
            "Parameters": {
                "CallId": call_id,
                "Digits": digits,
                "ToneDurationInMilliseconds": 250
            }
        })
        
    elif action_type == 'wait':
        duration_ms = step.get('duration_ms', 1000)
        print(f"Generating WAIT action: {duration_ms}ms")
        # Use SSML break
        # Note: Chime Speak action supports SSML if TextType is set to 'ssml'
        # The break tag max duration depends on the service but usually sufficient for pauses
        ssml = f"<speak><break time='{duration_ms}ms'/></speak>"
        actions.append({
            "Type": "Speak",
            "Parameters": {
                "Text": ssml,
                "Engine": "neural",
                "VoiceId": "Joanna",
                "CallId": call_id,
                "TextType": "ssml"
            }
        })
        
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
