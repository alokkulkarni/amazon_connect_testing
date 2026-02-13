import json

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    event_type = event.get('InvocationEventType')
    actions = []
    transaction_attributes = event.get('CallDetails', {}).get('TransactionAttributes', {}) or {}
    
    if event_type == 'NEW_OUTBOUND_CALL':
        # This event is triggered when we call create_sip_media_application_call
        print("Handling NEW_OUTBOUND_CALL")
        
        # Extract arguments passed from the Python test script
        # They come in event['ActionData']['Parameters'] for the initial invocation
        parameters = event.get('ActionData', {}).get('Parameters', {})
        tts_text = parameters.get('tts_text', 'Hello from Automation')
        
        # Store the text in TransactionAttributes so we can use it when the call is answered
        transaction_attributes['tts_text'] = tts_text
        
        # We don't perform actions yet, we let the call proceed to dial
        # Returning empty actions allows the call to ring
        
    elif event_type == 'CALL_ANSWERED':
        print("Handling CALL_ANSWERED")
        
        # Connect has answered the call. Now we speak.
        # Retrieve the text we stored earlier
        tts_text = transaction_attributes.get('tts_text', 'No text provided')
        
        print(f"Speaking: {tts_text}")
        
        # Speak action using Amazon Polly (built-in to Chime SDK)
        actions = [{
            "Type": "Speak",
            "Parameters": {
                "Text": tts_text,
                "Engine": "neural", # Optional: 'standard' or 'neural'
                "VoiceId": "Joanna" # Optional: Choose a voice
            }
        }]
        
    elif event_type == 'ACTION_SUCCESSFUL':
        print("Handling ACTION_SUCCESSFUL")
        
        # The Speak action finished.
        # We can either hang up or wait. Let's hang up to finish the test cleanly.
        last_action = event.get('ActionData', {}).get('Type')
        
        if last_action == 'Speak':
            actions = [{
                "Type": "Hangup",
                "Parameters": {
                    "SipResponseCode": "0",
                    "ParticipantTag": "LEG-A"
                }
            }]

    elif event_type == 'HANGUP':
        print("Handling HANGUP")
        # Call ended
        pass

    response = {
        "SchemaVersion": "1.0",
        "Actions": actions,
        "TransactionAttributes": transaction_attributes
    }
    
    print(f"Returning response: {json.dumps(response)}")
    return response
