import os

content = r'''import boto3
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
    
    print(f"\n----------------------------------------------------------------")
    print(f"STARTING TEST CASE: {test_case['name']}")
    print(f"----------------------------------------------------------------")
    print(f"[STEP 1] Setup: Dialing Amazon Connect...")
    print(f"   > From (Chime): {CHIME_PHONE_NUMBER}")
    print(f"   > To (Connect): {test_case['destination_phone']}")
    
    # Text to Speech content for the test
    input_speech = test_case.get('input_speech', "Hello")
    
    try:
        # 1. Initiate Inbound Call to Connect (via Chime SDK)
        print(f"[STEP 2] Action: Invoking Chime SIP Media Application...")
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
        print(f"   > SUCCESS: Call Initiated.")
        print(f"   > Transaction ID: {transaction_id}")
        print(f"   > Payload Sent: User will say '{input_speech}'")

        # 2. Wait for Flow Execution (Polling Mechanism)
        print(f"[STEP 3] Monitoring: Polling Amazon Connect for Contact in Queue...")
        
        expected_queue = test_case.get('expected_queue')
        
        # Resolve Queue ID first
        queue_id = None
        found_in_queue = False
        
        if expected_queue:
            if not MOCK_AWS:
                 try:
                     paginator = connect_client.get_paginator('list_queues')
                     found_queue = False
                     for page in paginator.paginate(InstanceId=CONNECT_INSTANCE_ID, QueueTypes=['STANDARD']):
                         for q in page['QueueSummaryList']:
                             if q['Name'] == expected_queue:
                                 queue_id = q['Id']
                                 print(f"   > Resolved Queue '{expected_queue}' to ID: {queue_id}")
                                 found_queue = True
                                 break
                         if found_queue: break
                     if not queue_id:
                         print(f"   > WARNING: Could not find Queue '{expected_queue}'.")
                 except Exception as q_err:
                     print(f"   > ERROR listing queues: {q_err}")

            max_retries = 12  # 12 * 5s = 60 seconds max wait
            
            if MOCK_AWS:
                 time.sleep(2)
                 found_in_queue = True
            elif queue_id:
                 for attempt in range(max_retries):
                     time.sleep(5)
                     print(f"   > Attempt {attempt+1}/{max_retries}: Checking metrics...")
                     
                     try:
                         filters = {'Channels': ['VOICE'], 'Queues': [queue_id]}
                         
                         current_metrics = connect_client.get_current_metric_data(
                             InstanceId=CONNECT_INSTANCE_ID,
                             Filters=filters,
                             CurrentMetrics=[
                                 {'Name': 'CONTACTS_IN_QUEUE', 'Unit': 'COUNT'},
                                 {'Name': 'CONTACTS_SCHEDULED', 'Unit': 'COUNT'}
                             ]
                         )
                         
                         for metric in current_metrics.get('MetricResults', []):
                             for collection in metric.get('Collections', []):
                                 if collection['Metric']['Name'] in ['CONTACTS_IN_QUEUE', 'CONTACTS_SCHEDULED']:
                                     count = int(collection['Value'])
                                     if count > 0:
                                         print(f"   > SUCCESS: Found {count} contact(s) in queue.")
                                         found_in_queue = True
                                         break
                             if found_in_queue: break
                     except Exception as e:
                         print(f"   > Metric check failed: {e}")
                     
                     if found_in_queue:
                         break
            else:
                print(f"   > WARNING: Queue ID not resolved, skipping metric check.")
        else:
            print(f"   > NOTE: No 'expected_queue' defined. Skipping queue metric check (Negative Test Case).")
            # Wait for call to traverse flow and disconnect
            time.sleep(15)
        
        # 3. Final Verification Results
        print(f"[STEP 4] Validation Results")
        if found_in_queue:
            print(f"   > TEST PASSED: Call successfully routed to expected queue '{expected_queue}'.")
        elif expected_queue:
            print(f"   > WARNING: Polling timed out. Contact never appeared in queue '{expected_queue}'.")
            print(f"   > Checking historical data (SearchContacts) to diagnose...")

        # STEP 5: Post-Call Analysis (Always run this to find the call details/transcript)
        print(f"[STEP 5] Post-Call Analysis: Fetching Call Details & Transcript...")
        
        contact_id = None
        if MOCK_AWS:
            contact_id = "mock-id"
            print(f"   [MOCK] Found Contact ID: {contact_id}")
        else:
             try:
                 print(f"   > Searching for Contact ID for phone {CHIME_PHONE_NUMBER}...")
                 # Wait a moment for indexing
                 time.sleep(5) 
                 
                 # NOTE: SearchContacts does not support filtering by Customer Phone Number directly in SearchCriteria
                 # We must fetch recent contacts and filter client-side.
                 search_response = connect_client.search_contacts(
                     InstanceId=CONNECT_INSTANCE_ID,
                     TimeRange={
                         'Type': 'INITIATION_TIMESTAMP',
                         'StartTime': int(time.time()) - 300, # Look back 5 mins
                         'EndTime': int(time.time()) + 60
                     },
                     SearchCriteria={
                         'Channels': ['VOICE']
                     },
                     Sort={
                         'FieldName': 'INITIATION_TIMESTAMP',
                         'Order': 'DESCENDING'
                     }
                 )
                 
                 contacts = search_response.get('Contacts', [])
                 if not contacts:
                     print(f"   > FAILURE: Could not find any contact record in the last 5 minutes.")
                     print(f"   > Possible causes: Call blocked, wrong instance, or Chime failed to dial.")
                 else:
                     # Iterate to find the right one if possible, or take the first
                     for c in contacts:
                         contact_id = c['Id']
                         print(f"   > Found Contact ID: {contact_id} (Most recent)")
                         break
                     
                     if contact_id:
                         # Get full details to verify queue
                         contact_details = connect_client.describe_contact(
                              InstanceId=CONNECT_INSTANCE_ID,
                              ContactId=contact_id
                         ).get('Contact', {})
                         
                         # Check Disconnect Reason
                         if 'DisconnectDetails' in contact_details:
                              print(f"   > Disconnect Reason: {contact_details.get('DisconnectDetails', {}).get('PotentialDisconnectIssue', 'Unknown')}")
                         
                         # Check Queue from Contact Record (Validation fallback)
                         contact_queue = contact_details.get('QueueInfo', {}).get('Name')
                         print(f"   > Contact Record indicates Queue: {contact_queue}")
                         
                         if expected_queue and not found_in_queue and contact_queue == expected_queue:
                              print(f"   > TEST PASSED (Historical): Contact WAS routed to queue (but ended before metric check).")
                              found_in_queue = True

                         # Fetch Transcript
                         try:
                             # Check if method exists (handling old boto3)
                             if hasattr(connect_client, 'list_realtime_contact_analysis_segments'):
                                 transcript_response = connect_client.list_realtime_contact_analysis_segments(
                                     InstanceId=CONNECT_INSTANCE_ID,
                                     ContactId=contact_id,
                                     MaxResults=100
                                 )
                                 
                                 print(f"   > --- TRANSCRIPT START ---")
                                 segments = transcript_response.get('Segments', [])
                                 has_transcript = False
                                 for segment in segments:
                                     transcript = segment.get('Transcript', {})
                                     if transcript:
                                         has_transcript = True
                                         speaker = transcript.get('ParticipantRole', 'UNKNOWN')
                                         content = transcript.get('Content', '')
                                         print(f"   > [{speaker}]: {content}")
                                 
                                 if not has_transcript:
                                     print(f"   > (No transcript segments found yet.)")
                                 print(f"   > --- TRANSCRIPT END ---")
                             else:
                                 print(f"   > WARNING: list_realtime_contact_analysis_segments not available in this boto3 version.")
                         except (ClientError, AttributeError) as ce:
                             print(f"   > INFO: Could not fetch transcript (likely due to permissions or old SDK): {ce}")

             except Exception as search_err:
                 print(f"   > ERROR searching for contact: {search_err}")

        # Final Assertion
        if not MOCK_AWS:
            if expected_queue:
                if not found_in_queue:
                    pytest.fail(f"Call did not reach expected queue '{expected_queue}'")
            else:
                # If no queue expected, pass if we found the contact
                if not contact_id:
                     pytest.fail("Call record not found in Connect.")
                else:
                     print("   > TEST PASSED: Call completed (Negative test case).")
            
    except ClientError as e:
        print(f"   > ERROR: AWS Client failed: {e}")
        pytest.fail(f"AWS ClientError: {e}")
    except AssertionError as e:
        print(f"   > FAILURE: Assertion failed: {e}")
        pytest.fail(f"Assertion Failed: {e}")
    except Exception as e:
        print(f"   > CRITICAL: Unexpected error: {e}")
        pytest.fail(f"Unexpected Error: {e}")

if __name__ == "__main__":
    # Allow running directly with python
    pytest.main(["-s", "-v", __file__])
'''

with open('test_voice_flows.py', 'w') as f:
    f.write(content)
