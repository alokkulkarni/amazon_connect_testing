import json
import boto3
import os
import time

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = "VoiceTestState"
table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    event_type = event.get('InvocationEventType')
    call_details = event.get('CallDetails', {})
    participants = call_details.get('Participants', [])
    transaction_id = call_details.get('TransactionId')
    
    # Retrieve conversation_id from TransactionAttributes (passed from start_call)
    transaction_attributes = call_details.get('TransactionAttributes', {})
    conversation_id = transaction_attributes.get('conversation_id')
    
    if not conversation_id:
        print("ERROR: No conversation_id found in TransactionAttributes")
        return {
            "SchemaVersion": "1.0",
            "Actions": [],
            "TransactionAttributes": transaction_attributes
        }

    # Fetch current state
    try:
        response = table.get_item(Key={'conversation_id': conversation_id})
        item = response.get('Item')
        
        if not item:
            print(f"ERROR: Conversation state not found for {conversation_id}")
            return {
                "SchemaVersion": "1.0", 
                "Actions": [],
                "TransactionAttributes": transaction_attributes
            }
            
        script = item.get('script', [])
        # Ensure script is a list
        if isinstance(script, str):
            try:
                script = json.loads(script)
            except:
                script = []
                
        current_step_index = int(item.get('current_step_index', 0))
        status = item.get('status', 'NEW')
        
    except Exception as e:
        print(f"DynamoDB Error: {e}")
        return {
            "SchemaVersion": "1.0", 
            "Actions": [],
            "TransactionAttributes": transaction_attributes
        }

    actions = []

    # Handle NEW_INBOUND_CALL (or OUTBOUND)
    if event_type in ['NEW_INBOUND_CALL', 'NEW_OUTBOUND_CALL', 'RINGING']:
        pass 

    # Handle ANSWERED or ACTION_SUCCESSFUL (Moving to next step)
    if event_type == 'CALL_ANSWERED' or (event_type == 'ACTION_SUCCESSFUL'):
        
        # If we just finished an action, increment step
        if event_type == 'ACTION_SUCCESSFUL':
            current_step_index += 1
            
        # Check if we have more steps
        if current_step_index < len(script):
            step = script[current_step_index]
            action_type = step.get('type')
            
            call_id = participants[0]['CallId'] if participants else None
            
            if action_type == 'speak':
                text = step.get('text')
                print(f"Executing Step {current_step_index}: Speak '{text}'")
                actions = [{
                    "Type": "Speak",
                    "Parameters": {
                        "Text": text,
                        "Engine": "neural",
                        "VoiceId": "Joanna",
                        "CallId": call_id
                    }
                }]
                
                # Update state
                table.update_item(
                    Key={'conversation_id': conversation_id},
                    UpdateExpression="set current_step_index = :i, #s = :st",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':i': current_step_index, ':st': 'IN_PROGRESS'}
                )
                
            elif action_type == 'wait':
                duration = step.get('duration_ms', 1000)
                print(f"Executing Step {current_step_index}: Wait {duration}ms")
                # Simulate wait by speaking silence (SSML)
                ssml_text = f"<speak><break time='{duration}ms'/></speak>"
                actions = [{
                    "Type": "Speak",
                    "Parameters": {
                        "Text": ssml_text,
                        "Engine": "neural",
                        "VoiceId": "Joanna",
                        "CallId": call_id,
                        "TextType": "ssml"
                    }
                }]
                
                table.update_item(
                    Key={'conversation_id': conversation_id},
                    UpdateExpression="set current_step_index = :i, #s = :st",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':i': current_step_index, ':st': 'IN_PROGRESS'}
                )

            elif action_type == 'hangup':
                 print(f"Executing Step {current_step_index}: Hangup")
                 actions = [{
                    "Type": "Hangup",
                    "Parameters": {
                        "SipResponseCode": "0",
                        "ParticipantTag": "LEG-A"
                    }
                }]
                 table.update_item(
                    Key={'conversation_id': conversation_id},
                    UpdateExpression="set #s = :st",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':st': 'COMPLETED'}
                )

        else:
            print("End of script. Hanging up.")
            actions = [{
                "Type": "Hangup",
                "Parameters": {
                    "SipResponseCode": "0",
                    "ParticipantTag": "LEG-A"
                }
            }]
            table.update_item(
                Key={'conversation_id': conversation_id},
                UpdateExpression="set #s = :st",
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':st': 'COMPLETED'}
            )

    elif event_type == 'HANGUP':
        print("Call ended.")
        table.update_item(
            Key={'conversation_id': conversation_id},
            UpdateExpression="set #s = :st",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':st': 'COMPLETED'}
        )

    return {
        "SchemaVersion": "1.0",
        "Actions": actions,
        "TransactionAttributes": transaction_attributes
    }
