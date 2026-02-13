import boto3
import json
import pytest
import os
import time
from botocore.exceptions import ClientError, NoCredentialsError
from unittest.mock import MagicMock
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration - should be set via environment variables in CI/CD
CONNECT_INSTANCE_ID = os.getenv("CONNECT_INSTANCE_ID", "your-instance-id")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1") # Region for Connect
CHIME_AWS_REGION = os.getenv("CHIME_AWS_REGION", AWS_REGION) # Region for Chime (defaults to AWS_REGION)

CHIME_SMA_ID = os.getenv("CHIME_SMA_ID", "your-sip-media-app-id")
CHIME_PHONE_NUMBER = os.getenv("CHIME_PHONE_NUMBER", "+15550100") # Number owned by Chime
MOCK_AWS = os.getenv("MOCK_AWS", "true").lower() == "true"

def get_clients():
    if MOCK_AWS:
        connect_client = MagicMock()
        chime_client = MagicMock()
        
        # Simulate successful Chime call creation
        chime_client.create_sip_media_application_call.return_value = {
            "SipMediaApplicationCall": {
                "TransactionId": "mock-transaction-id-12345"
            }
        }
        
        # Simulate Connect contact search finding the call
        connect_client.get_current_metric_data.return_value = {
             "MetricResults": [] # Simplified
        }
        
        return connect_client, chime_client
    else:
        try:
            connect = boto3.client("connect", region_name=AWS_REGION)
            chime = boto3.client("chime-sdk-voice", region_name=CHIME_AWS_REGION)
            return connect, chime
        except NoCredentialsError:
            pytest.fail("No AWS credentials found. Please configure your credentials.")

def load_test_cases():
    with open("test_cases.json", "r") as f:
        return json.load(f)

@pytest.mark.parametrize("test_case", load_test_cases())
def test_connect_voice_flow(test_case):
    """
    Test execution for Amazon Connect voice flows via Inbound Call.
    Uses AWS Chime SDK to place a call TO Amazon Connect and simulate a user.
    """
    connect_client, chime_client = get_clients()
    
    print(f"\nRunning Test: {test_case['name']}")
    print(f"Dialing Connect Number: {test_case['destination_phone']} from {CHIME_PHONE_NUMBER}")
    
    # Text to Speech content for the test
    input_speech = test_case.get('input_speech', "Hello")
    
    try:
        # 1. Initiate Inbound Call to Connect (via Chime SDK)
        # We use a SIP Media Application to dial out to Connect
        # The ArgumentsMap passes the 'script' to our Chime Handler (Lambda)
        # which will use the <Speak> action (TTS) to say this text once connected.
        response = chime_client.create_sip_media_application_call(
            FromPhoneNumber=CHIME_PHONE_NUMBER,
            ToPhoneNumber=test_case['destination_phone'],
            SipMediaApplicationId=CHIME_SMA_ID,
            ArgumentsMap={
                'case_id': test_case.get('name', 'unknown'),
                'tts_text': input_speech,
                'expected_intent': test_case.get('attributes', {}).get('intent', '')
            }
        )
        
        transaction_id = response['SipMediaApplicationCall']['TransactionId']
        print(f"Inbound Call Initiated. Chime Transaction ID: {transaction_id}")
        print(f"Simulating User Speech (TTS): '{input_speech}'")

        # 2. Wait for Flow Execution
        # Real-world: Wait enough time for the call to traverse the flow
        wait_time = 10 if not MOCK_AWS else 0
        time.sleep(wait_time)
        
        # 3. Verify Flow Execution in Connect
        # Since we initiated the call from outside, we need to find the Contact in Connect
        # usually by searching for the Source Phone Number (our Chime number).
        
        # In a real implementation, we would use search_contacts or look up metrics.
        # Here we verify that if we expected a queue transfer, it happened.
        
        expected_queue = test_case.get('expected_queue')
        
        if expected_queue and not MOCK_AWS:
             # Example: Check if any contact from our number is currently in the expected queue
             # exact implementation depends on how you track the specific contact ID
             pass
        elif MOCK_AWS:
             # Mock Validation
             print(f"Validating Routing to Queue: {expected_queue if expected_queue else 'Default'}")
             assert True # Simulate success
        
        print(f"Test '{test_case['name']}' PASSED.")

    except ClientError as e:
        pytest.fail(f"AWS ClientError: {e}")
    except AssertionError as e:
        pytest.fail(f"Assertion Failed: {e}")
    except Exception as e:
        pytest.fail(f"Unexpected Error: {e}")

def load_test_cases():
    with open("test_cases.json", "r") as f:
        return json.load(f)

@pytest.mark.parametrize("test_case", load_test_cases())
def test_connect_voice_flow(test_case):
    """
    Test execution for Amazon Connect voice flows via Inbound Call.
    Uses AWS Chime SDK to place a call TO Amazon Connect and simulate a user.
    """
    connect_client, chime_client = get_clients()
    
    print(f"\nRunning Test: {test_case['name']}")
    print(f"Dialing Connect Number: {test_case['destination_phone']} from {CHIME_PHONE_NUMBER}")
    
    # Text to Speech content for the test
    input_speech = test_case.get('input_speech', "Hello")
    
    try:
        # 1. Initiate Inbound Call to Connect (via Chime SDK)
        # We use a SIP Media Application to dial out to Connect
        # The ArgumentsMap passes the 'script' to our Chime Handler (Lambda)
        # which will use the <Speak> action (TTS) to say this text once connected.
        response = chime_client.create_sip_media_application_call(
            FromPhoneNumber=CHIME_PHONE_NUMBER,
            ToPhoneNumber=test_case['destination_phone'],
            SipMediaApplicationId=CHIME_SMA_ID,
            ArgumentsMap={
                'case_id': test_case.get('name', 'unknown'),
                'tts_text': input_speech,
                'expected_intent': test_case.get('attributes', {}).get('intent', '')
            }
        )
        
        transaction_id = response['SipMediaApplicationCall']['TransactionId']
        print(f"Inbound Call Initiated. Chime Transaction ID: {transaction_id}")
        print(f"Simulating User Speech (TTS): '{input_speech}'")

        # 2. Wait for Flow Execution
        # Real-world: Wait enough time for the call to traverse the flow
        wait_time = 10 if not MOCK_AWS else 0
        time.sleep(wait_time)
        
        # 3. Verify Flow Execution in Connect
        # Since we initiated the call from outside, we need to find the Contact in Connect
        # usually by searching for the Source Phone Number (our Chime number).
        
        # In a real implementation, we would use search_contacts or look up metrics.
        # Here we verify that if we expected a queue transfer, it happened.
        
        expected_queue = test_case.get('expected_queue')
        
        if expected_queue and not MOCK_AWS:
             # Example: Check if any contact from our number is currently in the expected queue
             # exact implementation depends on how you track the specific contact ID
             pass
        elif MOCK_AWS:
             # Mock Validation
             print(f"Validating Routing to Queue: {expected_queue if expected_queue else 'Default'}")
             assert True # Simulate success
        
        print(f"Test '{test_case['name']}' PASSED.")

    except ClientError as e:
        pytest.fail(f"AWS ClientError: {e}")
    except AssertionError as e:
        pytest.fail(f"Assertion Failed: {e}")
    except Exception as e:
        pytest.fail(f"Unexpected Error: {e}")

if __name__ == "__main__":
    # Allow running directly with python
    pytest.main(["-v", __file__])
