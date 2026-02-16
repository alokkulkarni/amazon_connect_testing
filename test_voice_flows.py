import boto3
import pytest
import json
import os
import time
import uuid
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
CONNECT_INSTANCE_ID = os.getenv('CONNECT_INSTANCE_ID', '96a4166c-3e1f-44e1-bfea-82f88582b0d7')
CHIME_SMA_ID = os.getenv('CHIME_SMA_ID', '2c029a7a-92cf-45b7-be78-94ae50be7a00')
CHIME_PHONE_NUMBER = os.getenv('CHIME_PHONE_NUMBER', '+441134711044')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
DYNAMODB_TABLE_NAME = "VoiceTestState"

# Mock mode for GitHub Actions or local dev without full creds
MOCK_AWS = os.getenv('MOCK_AWS', 'false').lower() == 'true'

def get_clients():
    """Returns AWS clients."""
    if MOCK_AWS:
        return None, None, None
    
    session = boto3.Session(region_name=AWS_REGION)
    connect_client = session.client('connect')
    chime_client = session.client('chime-sdk-voice')
    dynamodb_client = session.resource('dynamodb')
    return connect_client, chime_client, dynamodb_client

def load_test_cases():
    """Loads test cases from JSON file."""
    with open('test_cases.json', 'r') as f:
        return json.load(f)

def deploy_infrastructure_if_needed():
    """Runs the deployment script to ensure DynamoDB and Lambda are ready."""
    if MOCK_AWS:
        print("[MOCK] Skipping infrastructure deployment.")
        return

    print("Checking infrastructure...")
    try:
        import deploy_infrastructure
        deploy_infrastructure.create_dynamodb_table()
        deploy_infrastructure.update_lambda_code()
    except Exception as e:
        print(f"Warning: Infrastructure deployment failed or skipped: {e}")


# Initialize infrastructure once per session
@pytest.fixture(scope="session", autouse=True)
def setup_infrastructure():
    deploy_infrastructure_if_needed()

@pytest.mark.parametrize("test_case", load_test_cases())
def test_connect_voice_flow(test_case):
    """
    Test execution for Amazon Connect voice flows via Multi-Turn Conversation.
    """
    connect_client, chime_client, dynamodb = get_clients()

    print(f"\n----------------------------------------------------------------")
    print(f"STARTING TEST CASE: {test_case['name']}")
    print(f"----------------------------------------------------------------")
    
    # 1. Seed Conversation State
    conversation_id = str(uuid.uuid4())
    print(f"[STEP 1] Setup: Seeding conversation {conversation_id} in DynamoDB...")
    
    # Check if 'script' is present (new format) or 'input_speech' (old format fallback)
    script = test_case.get('script')
    if not script and 'input_speech' in test_case:
        # Convert old format to simple script
        script = [
            {"type": "wait", "duration_ms": 2000},
            {"type": "speak", "text": test_case['input_speech']},
            {"type": "wait", "duration_ms": 10000} # Wait for routing
        ]
    
    if not MOCK_AWS:
        try:
            table = dynamodb.Table(DYNAMODB_TABLE_NAME)
            # Use strict types for float/decimal to avoid dynamo issues
            # Script might contain floats for duration, better to ensure they are decimals or ints
            # Boto3 handles int/float well usually, but Decimal is safer for DynamoDB
            table.put_item(Item={
                'conversation_id': conversation_id,
                'script': json.dumps(script), # Store as JSON string to avoid attribute type issues
                'current_step_index': 0,
                'status': 'READY',
                'created_at': int(time.time())
            })
        except Exception as e:
            print(f"   > ERROR seeding DynamoDB: {e}")
            pytest.fail(f"Failed to seed DynamoDB state: {e}")
    
    print(f"   > From (Chime): {CHIME_PHONE_NUMBER}")
    print(f"   > To (Connect): {test_case['destination_phone']}")

    transaction_id = None
    try:
        # 2. Initiate Call
        print(f"[STEP 2] Action: Invoking Chime SIP Media Application...")
        
        if not MOCK_AWS:
            response = chime_client.create_sip_media_application_call(
                FromPhoneNumber=CHIME_PHONE_NUMBER,
                ToPhoneNumber=test_case['destination_phone'],
                SipMediaApplicationId=CHIME_SMA_ID,
                ArgumentsMap={
                    'conversation_id': conversation_id,
                    'case_id': test_case.get('name', 'unknown')
                }
            )
            transaction_id = response['SipMediaApplicationCall']['TransactionId']
            print(f"   > SUCCESS: Call Initiated. Transaction ID: {transaction_id}")
        else:
            print(f"   > [MOCK] Call Initiated.")

        # 3. Monitor Conversation Progress
        print(f"[STEP 3] Monitoring: Watching conversation progress in DynamoDB...")
        
        if not MOCK_AWS:
            # Poll DynamoDB until status is COMPLETED or timeout
            # Calculate total expected duration based on script waits
            script_duration = sum([s.get('duration_ms', 0) for s in script]) / 1000
            max_wait = max(60, script_duration + 30) # Buffer
            
            start_time = time.time()
            conversation_completed = False
            last_step = -1
            
            while time.time() - start_time < max_wait:
                try:
                    resp = table.get_item(Key={'conversation_id': conversation_id}, ConsistentRead=True)
                    item = resp.get('Item', {})
                    status = item.get('status')
                    step = int(item.get('current_step_index', 0))
                    
                    if step != last_step:
                        print(f"   > Status: {status}, Step: {step}/{len(script)}")
                        last_step = step
                    
                    if status == 'COMPLETED':
                        conversation_completed = True
                        print("   > Conversation script completed.")
                        break
                    
                    time.sleep(2)
                except Exception as e:
                    print(f"   > Warning polling DynamoDB: {e}")
                    time.sleep(2)
                
            if not conversation_completed:
                print("   > WARNING: Conversation did not complete within timeout.")
        else:
            time.sleep(2)

        # 4. Validate Routing (Metric Check)
        print(f"[STEP 4] Validation: Checking Amazon Connect Queue metrics...")
        expected_queue = test_case.get('expected_queue')
        
        found_in_queue = False
        queue_id = None
        
        if expected_queue and not MOCK_AWS:
            # Resolve Queue ID
            try:
                paginator = connect_client.get_paginator('list_queues')
                found = False
                for page in paginator.paginate(InstanceId=CONNECT_INSTANCE_ID, QueueTypes=['STANDARD']):
                    for q in page['QueueSummaryList']:
                        if q['Name'] == expected_queue:
                            queue_id = q['Id']
                            print(f"   > Resolved Queue '{expected_queue}' to ID: {queue_id}")
                            found = True
                            break
                    if found: break
            except Exception as e:
                print(f"   > Error resolving queue: {e}")
            
            if queue_id:
                # Poll metrics
                for attempt in range(6):
                    print(f"   > Checking metrics (Attempt {attempt+1}/6)...")
                    try:
                        metrics = connect_client.get_current_metric_data(
                            InstanceId=CONNECT_INSTANCE_ID,
                            Filters={'Channels': ['VOICE'], 'Queues': [queue_id]},
                            CurrentMetrics=[{'Name': 'CONTACTS_IN_QUEUE', 'Unit': 'COUNT'}]
                        )
                        for m in metrics.get('MetricResults', []):
                            count = int(m['Collections'][0]['Value'])
                            if count > 0:
                                print(f"   > SUCCESS: Found {count} contact(s) in queue.")
                                found_in_queue = True
                                break
                    except Exception as e: 
                        print(f"   > Metric check error: {e}")
                    
                    if found_in_queue: break
                    time.sleep(5)

        if found_in_queue:
            print(f"   > TEST PASSED: Contact found in queue '{expected_queue}'.")
        elif expected_queue:
            print(f"   > WARNING: Contact not found in queue metrics. Checking historical traces...")

        # 5. Post-Call Analysis (Historical Trace)
        print(f"[STEP 5] Post-Call Analysis...")
        
        if not MOCK_AWS and expected_queue:
            try:
                print(f"   > Searching for Contact ID for phone {CHIME_PHONE_NUMBER}...")
                time.sleep(5) # Allow indexing
                
                # Fetch recent contacts
                search_response = connect_client.search_contacts(
                     InstanceId=CONNECT_INSTANCE_ID,
                     TimeRange={
                         'Type': 'INITIATION_TIMESTAMP',
                         'StartTime': int(time.time()) - 300, 
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
                if contacts:
                    contact = contacts[0]
                    contact_id = contact['Id']
                    print(f"   > Found Contact ID: {contact_id}")
                    
                    # Verify queue from contact record
                    contact_queue = contact.get('QueueInfo', {}).get('Name')
                    print(f"   > Contact routed to: {contact_queue}")
                    
                    if contact_queue == expected_queue:
                        print(f"   > TEST PASSED (Historical): Contact confirmed in queue '{expected_queue}'.")
                        found_in_queue = True
                    
                    # Get Transcript
                    try:
                        # Check if method exists (handling old boto3)
                        if hasattr(connect_client, 'list_realtime_contact_analysis_segments'):
                            transcript_resp = connect_client.list_realtime_contact_analysis_segments(
                                InstanceId=CONNECT_INSTANCE_ID,
                                ContactId=contact_id,
                                MaxResults=100
                            )
                            print("   > --- TRANSCRIPT ---")
                            for seg in transcript_resp.get('Segments', []):
                                trans = seg.get('Transcript', {})
                                if trans:
                                    print(f"   > [{trans.get('ParticipantRole')}]: {trans.get('Content')}")
                            print("   > ------------------")
                    except Exception as e:
                        print(f"   > Could not fetch transcript: {e}")
                else:
                    print("   > No recent contacts found.")

            except Exception as e:
                print(f"   > Historical search failed: {e}")

        # Final Assertion
        if expected_queue and not found_in_queue and not MOCK_AWS:
             # Strict failure if neither metric nor history confirmed it
             pytest.fail(f"Call did not reach expected queue '{expected_queue}'")

    except Exception as e:
        print(f"   > ERROR: {e}")
        if not MOCK_AWS:
            pytest.fail(f"Test exception: {e}")
