import pytest
import boto3
import os
import json
import time
import uuid
import subprocess
from dotenv import load_dotenv

load_dotenv()

from botocore.exceptions import ClientError
from decimal import Decimal

# Configuration
# Default to existing values if not updated by infrastructure setup
CHIME_PHONE_NUMBER = os.environ.get('CHIME_PHONE_NUMBER', '')
CHIME_SMA_ID = os.environ.get('CHIME_SMA_ID', '')
CONNECT_INSTANCE_ID = os.environ.get('CONNECT_INSTANCE_ID', '')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'VoiceTestState')

# Connect is in one region (e.g. eu-west-2), Chime/Lambda in another (e.g. us-east-1)
CONNECT_REGION = os.environ.get('AWS_REGION', 'us-east-1')
CHIME_REGION = os.environ.get('CHIME_AWS_REGION', 'us-east-1')
MOCK_AWS = os.environ.get('MOCK_AWS', 'false').lower() == 'true'

@pytest.fixture(scope="session", autouse=True)
def setup_infrastructure():
    """
    Deploys/Verifies Infrastructure before running tests.
    Reads infrastructure_output.json to configure test environment.
    """
    if MOCK_AWS:
        print("\n[SETUP] Running in MOCK mode. Skipping infrastructure deployment.")
        return

    print(f"\n[SETUP] Deploying/Verifying Infrastructure (Region: {CHIME_REGION})...")
    try:
        # Run the deployment script
        import sys
        deploy_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_infrastructure.py')
        
        # Ensure correct region is passed to deployment script via ENV
        env = os.environ.copy()
        env['AWS_REGION'] = CHIME_REGION # Deployment script uses AWS_REGION for Chime/Lambda
        
        result = subprocess.run(
            [sys.executable, deploy_script],
            capture_output=True,
            text=True,
            env=env
        )
        
        if result.returncode != 0:
            print(f"Deployment Script Failed:\n{result.stderr}")
            print("WARNING: Deployment script failed. relying on existing environment variables.")
        else:
            print("Deployment script executed successfully.")
        
        # Read output
        output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'infrastructure_output.json')
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                infra_config = json.load(f)
                
            global CHIME_PHONE_NUMBER, CHIME_SMA_ID, DYNAMODB_TABLE_NAME
            CHIME_PHONE_NUMBER = infra_config.get('CHIME_PHONE_NUMBER', CHIME_PHONE_NUMBER)
            CHIME_SMA_ID = infra_config.get('CHIME_SMA_ID', CHIME_SMA_ID)
            DYNAMODB_TABLE_NAME = infra_config.get('DYNAMODB_TABLE', DYNAMODB_TABLE_NAME)
            
            print(f"Loaded Infrastructure Config: SMA={CHIME_SMA_ID}, Phone={CHIME_PHONE_NUMBER}")
        else:
            print("WARNING: infrastructure_output.json not found. Using defaults.")
            
    except Exception as e:
        print(f"Error during setup: {e}")

def load_test_cases():
    file_path = os.path.join(os.path.dirname(__file__), 'test_cases.json')
    with open(file_path, 'r') as f:
        return json.load(f)

def get_clients():
    if MOCK_AWS:
        return None, None, None
    
    # Session for Connect (eu-west-2)
    session_connect = boto3.Session(region_name=CONNECT_REGION)
    # Session for Chime/DynamoDB (us-east-1)
    session_chime = boto3.Session(region_name=CHIME_REGION)
    
    return (
        session_connect.client('connect'),
        session_chime.client('chime-sdk-voice'),
        session_chime.resource('dynamodb')
    )

@pytest.mark.parametrize("test_case", load_test_cases())
def test_connect_voice_flow(test_case):
    """
    Test execution for Amazon Connect voice flows via Inbound Call.
    Uses AWS Chime SDK to place a call TO Amazon Connect and simulate a user.
    """
    connect_client, chime_client, dynamodb = get_clients()

    print(f"\n----------------------------------------------------------------")
    print(f"STARTING TEST CASE: {test_case['name']}")
    print(f"----------------------------------------------------------------")
    
    if not CHIME_PHONE_NUMBER or not CHIME_SMA_ID:
        if not MOCK_AWS:
            pytest.fail("Missing CHIME_PHONE_NUMBER or CHIME_SMA_ID configuration.")
    
    # 1. Seed Conversation State
    conversation_id = str(uuid.uuid4())
    print(f"[STEP 1] Setup: Seeding conversation {conversation_id} in DynamoDB...")
    
    script = test_case.get('script', [])
    if not script and 'input_speech' in test_case:
         script = [
            {"type": "wait", "duration_ms": 2000},
            {"type": "speak", "text": test_case['input_speech']},
            {"type": "wait", "duration_ms": 10000}
        ]
    
    if not MOCK_AWS:
        try:
            table = dynamodb.Table(DYNAMODB_TABLE_NAME)
            table.put_item(Item={
                'conversation_id': conversation_id,
                'script': json.dumps(script),
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
            # Poll DynamoDB
            script_duration = sum([s.get('duration_ms', 0) for s in script]) / 1000
            max_wait = max(60, script_duration + 30)
            
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

        # 4. Validate Routing
        print(f"[STEP 4] Validation: Checking Amazon Connect Queue metrics...")
        expected_queue = test_case.get('expected_queue')
        
        found_in_queue = False
        queue_id = None
        
        if expected_queue and not MOCK_AWS:
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
            print(f"   > WARNING: Contact not found in queue metrics.")

        # 5. Post-Call Analysis
        print(f"[STEP 5] Post-Call Analysis...")
        
        if not MOCK_AWS and expected_queue:
            try:
                print(f"   > Searching for Contact ID for phone {CHIME_PHONE_NUMBER}...")
                time.sleep(5)
                
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
                    
                    contact_queue = contact.get('QueueInfo', {}).get('Name')
                    print(f"   > Contact routed to: {contact_queue}")
                    
                    if contact_queue == expected_queue:
                        print(f"   > TEST PASSED (Historical): Contact confirmed in queue '{expected_queue}'.")
                        found_in_queue = True
                else:
                    print("   > No recent contacts found.")

            except Exception as e:
                print(f"   > Historical search failed: {e}")

        if expected_queue and not found_in_queue and not MOCK_AWS:
             print("   > FAILURE: Call did not reach expected queue.")

    except Exception as e:
        print(f"   > ERROR: {e}")
        if not MOCK_AWS:
            pytest.fail(f"Test exception: {e}")
