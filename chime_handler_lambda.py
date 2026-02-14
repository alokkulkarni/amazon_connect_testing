import json

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    event_type = event.get('InvocationEventType')
    actions = []
    transaction_attributes = event.get('CallDetails', {}).get('TransactionAttributes', {}) or {}
    
    if event_type == 'NEW_OUTBOUND_CALL':
        # This event is triggered when we call create_sip_media_application_call
        print("Handling NEW_OUTBOUND_CALL")
        
        # When invoked via create_sip_media_application_call, arguments from ArgumentsMap 
        # are available in CallDetails.TransactionAttributes directly.
        # However, sometimes they are nested or not immediately available in the top-level event dict
        # We should check both locations.
        
        call_details = event.get('CallDetails', {})
        transaction_attributes = call_details.get('TransactionAttributes', {})
        
        # Check if tts_text is already in transaction_attributes (from ArgumentsMap)
        tts_text = transaction_attributes.get('tts_text')
        
        if not tts_text:
            # Fallback if not found (e.g. if invoked differently)
            parameters = event.get('ActionData', {}).get('Parameters', {})
            tts_text = parameters.get('tts_text', 'Hello from Automation')
            # Store it for later events
            transaction_attributes['tts_text'] = tts_text
        
        print(f"Text to speech set to: {tts_text}")
        
        # We don't perform actions yet, we let the call proceed to dial
        # Returning empty actions allows the call to ring
        actions = []
        
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
        
        last_action = event.get('ActionData', {}).get('Type')
        print(f"Last action type: {last_action}")
        
        if last_action == 'Speak':
            # After speaking, wait for 60 seconds to allow the test script to verify
            # that the call reached the queue. If we hang up immediately, the contact
            # leaves the queue and the test fails.
            print("Speak finished. Pausing for 60s to keep call active in queue.")
            actions = [{
                "Type": "Pause",
                "Parameters": {
                    "DurationInMilliseconds": "60000"
                }
            }]
            
        elif last_action == 'Pause':
            # After the pause, now we can hang up.
            print("Pause finished. Hanging up.")
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
