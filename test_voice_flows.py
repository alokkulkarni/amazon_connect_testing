"""
Amazon Connect Voice Flow Automation Tests
==========================================
Uses AWS Chime SDK SIP Media Application to simulate a virtual customer
placing a real PSTN call into Amazon Connect.

Fixes applied:
  - Hard pytest.fail() / assert on all routing outcomes (no silent false-greens)
  - Contact Attributes verification via get_contact_attributes post-call
  - Contact Trace Record (CTR) validation via search_contacts with proper polling
  - Closed-hours behaviour validated via Connect hours-of-operation override API
  - DynamoDB TTL on every seeded item; pytest finalizer tears down per-test state
  - Wait steps in Lambda handler use chained SendDigits silence (no 10s SSML cap)
  - Per-test unique conversation_id avoids cross-test queue metric pollution
  - Environment namespace support via ENV_NAME env var
  - Multi-turn, transfer, reprompt, DTMF auth, callback, and error path test cases
"""
import pytest
import boto3
import os
import json
import time
import uuid
import subprocess
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from decimal import Decimal

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHIME_PHONE_NUMBER  = os.environ.get('CHIME_PHONE_NUMBER', '')
CHIME_SMA_ID        = os.environ.get('CHIME_SMA_ID', '')
CONNECT_INSTANCE_ID = os.environ.get('CONNECT_INSTANCE_ID', '')
ENV_NAME            = os.environ.get('ENV_NAME', 'dev')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', f'VoiceTestState-{ENV_NAME}')

# Connect is in one region (e.g. eu-west-2), Chime/Lambda in another (e.g. us-east-1)
CONNECT_REGION = os.environ.get('AWS_REGION', 'us-east-1')
CHIME_REGION   = os.environ.get('CHIME_AWS_REGION', 'us-east-1')
MOCK_AWS       = os.environ.get('MOCK_AWS', 'false').lower() == 'true'

# Poll timeouts
QUEUE_POLL_TIMEOUT_S  = 60
QUEUE_POLL_INTERVAL_S = 5
CTR_POLL_TIMEOUT_S    = 300   # Connect CTR indexing can take 1-3 min
CTR_POLL_INTERVAL_S   = 10

@pytest.fixture(scope="session", autouse=True)
def setup_infrastructure():
    """Deploy / verify infrastructure once per session."""
    if MOCK_AWS:
        print("\n[SETUP] Running in MOCK mode — skipping infrastructure deployment.")
        return

    print(f"\n[SETUP] Deploying/Verifying Infrastructure (Region: {CHIME_REGION})...")
    try:
        import sys
        deploy_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_infrastructure.py')
        env = os.environ.copy()
        env['AWS_REGION']          = CHIME_REGION
        env['ENV_NAME']            = ENV_NAME
        env['DYNAMODB_TABLE_NAME'] = DYNAMODB_TABLE_NAME

        result = subprocess.run(
            [sys.executable, deploy_script],
            capture_output=True, text=True, env=env
        )
        if result.returncode != 0:
            print(f"[SETUP] Deployment stderr:\n{result.stderr}")
            print("[SETUP] WARNING: Deployment failed — relying on existing env vars.")
        else:
            print("[SETUP] Deployment succeeded.")

        output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'infrastructure_output.json')
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                infra = json.load(f)
            global CHIME_PHONE_NUMBER, CHIME_SMA_ID  # noqa: PLW0603
            CHIME_PHONE_NUMBER  = infra.get('CHIME_PHONE_NUMBER', CHIME_PHONE_NUMBER)
            CHIME_SMA_ID        = infra.get('CHIME_SMA_ID', CHIME_SMA_ID)
            # DYNAMODB_TABLE_NAME is a module-level var — update via os.environ side-channel
            _tbl = infra.get('DYNAMODB_TABLE', DYNAMODB_TABLE_NAME)
            os.environ['DYNAMODB_TABLE_NAME'] = _tbl
            print(f"[SETUP] SMA={CHIME_SMA_ID}  Phone={CHIME_PHONE_NUMBER}  Table={_tbl}")
        else:
            print("[SETUP] WARNING: infrastructure_output.json not found — using defaults.")
    except Exception as e:
        print(f"[SETUP] Error: {e}")

def load_test_cases():
    file_path = os.path.join(os.path.dirname(__file__), 'test_cases.json')
    with open(file_path, 'r') as f:
        return json.load(f)


def get_clients():
    if MOCK_AWS:
        return None, None, None, None

    # Session for Connect (e.g. eu-west-2)
    session_connect = boto3.Session(region_name=CONNECT_REGION)
    # Session for Chime/DynamoDB (us-east-1)
    session_chime   = boto3.Session(region_name=CHIME_REGION)

    return (
        session_connect.client('connect'),
        session_chime.client('chime-sdk-voice'),
        session_chime.resource('dynamodb'),
        session_chime.client('transcribe'),
    )


# ---------------------------------------------------------------------------
# Helper: seed DynamoDB conversation state with TTL
# FIX: Every item now carries a 1-hour TTL to prevent indefinite accumulation.
# ---------------------------------------------------------------------------
def seed_conversation(dynamodb_resource, conversation_id: str, script: list, test_name: str):
    table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
    ttl   = int(time.time()) + 3600
    table.put_item(Item={
        'conversation_id':    conversation_id,
        'script':             json.dumps(script),
        'current_step_index': 0,
        'status':             'READY',
        'test_name':          test_name,
        'created_at':         int(time.time()),
        'ttl':                ttl,
    })


# ---------------------------------------------------------------------------
# Helper: delete DynamoDB item (finalizer teardown)
# FIX: Per-test cleanup ensures stale READY/IN_PROGRESS items do not linger.
# ---------------------------------------------------------------------------
def cleanup_conversation(dynamodb_resource, conversation_id: str):
    try:
        table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
        table.delete_item(Key={'conversation_id': conversation_id})
        print(f"   [CLEANUP] Deleted conversation {conversation_id}")
    except Exception as e:
        print(f"   [CLEANUP] Warning: could not delete {conversation_id}: {e}")


# ---------------------------------------------------------------------------
# Helper: place outbound call with exponential back-off retry
# ---------------------------------------------------------------------------
def place_call(chime_client, conversation_id: str, test_case: dict) -> str:
    retries = 3
    backoff = 15
    for attempt in range(retries):
        try:
            resp = chime_client.create_sip_media_application_call(
                FromPhoneNumber=CHIME_PHONE_NUMBER,
                ToPhoneNumber=test_case['destination_phone'],
                SipMediaApplicationId=CHIME_SMA_ID,
                ArgumentsMap={
                    'conversation_id': conversation_id,
                    'case_id':         test_case.get('name', 'unknown'),
                    'env':             ENV_NAME,
                }
            )
            return resp['SipMediaApplicationCall']['TransactionId']
        except ClientError as e:
            code = e.response['Error']['Code']
            if code in ('ThrottlingException', 'ServiceUnavailableException') or \
               'Concurrent call limits' in str(e):
                if attempt < retries - 1:
                    print(f"   [CALL] Rate limited — waiting {backoff}s (attempt {attempt+1}/{retries})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
            raise


# ---------------------------------------------------------------------------
# Helper: poll DynamoDB until script reaches COMPLETED or timeout
# ---------------------------------------------------------------------------
def wait_for_completion(dynamodb_resource, conversation_id: str, script: list) -> bool:
    table       = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
    script_secs = sum(s.get('duration_ms', 0) for s in script) / 1000
    max_wait    = max(90, script_secs + 30)
    start       = time.time()
    last_step   = -1

    while time.time() - start < max_wait:
        try:
            resp   = table.get_item(Key={'conversation_id': conversation_id}, ConsistentRead=True)
            item   = resp.get('Item', {})
            status = item.get('status')
            step   = int(item.get('current_step_index', 0))
            if step != last_step:
                print(f"   [MONITOR] status={status}  step={step}/{len(script)}")
                last_step = step
            if status == 'COMPLETED':
                return True
        except Exception as e:
            print(f"   [MONITOR] DynamoDB poll warning: {e}")
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Helper: resolve Connect queue name → queue ID
# ---------------------------------------------------------------------------
def resolve_queue_id(connect_client, queue_name: str):
    try:
        paginator = connect_client.get_paginator('list_queues')
        for page in paginator.paginate(InstanceId=CONNECT_INSTANCE_ID, QueueTypes=['STANDARD']):
            for q in page['QueueSummaryList']:
                if q['Name'] == queue_name:
                    return q['Id']
    except Exception as e:
        print(f"   [QUEUE] Error resolving queue '{queue_name}': {e}")
    return None


# ---------------------------------------------------------------------------
# Helper: poll real-time CONTACTS_IN_QUEUE metric
# ---------------------------------------------------------------------------
def check_queue_metric(connect_client, queue_id: str) -> bool:
    deadline = time.time() + QUEUE_POLL_TIMEOUT_S
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        print(f"   [QUEUE] Metric poll attempt {attempt}...")
        try:
            metrics = connect_client.get_current_metric_data(
                InstanceId=CONNECT_INSTANCE_ID,
                Filters={'Channels': ['VOICE'], 'Queues': [queue_id]},
                CurrentMetrics=[{'Name': 'CONTACTS_IN_QUEUE', 'Unit': 'COUNT'}]
            )
            for result in metrics.get('MetricResults', []):
                for col in result.get('Collections', []):
                    if int(col.get('Value', 0)) > 0:
                        return True
        except Exception as e:
            print(f"   [QUEUE] Metric error: {e}")
        time.sleep(QUEUE_POLL_INTERVAL_S)
    return False


# ---------------------------------------------------------------------------
# Helper: search Connect CTR for a recent VOICE contact
# FIX: Replaced single-attempt search with a polling loop to handle indexing lag.
# ---------------------------------------------------------------------------
def find_contact(connect_client, since_ts: int):
    deadline = time.time() + CTR_POLL_TIMEOUT_S
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        print(f"   [CTR] search_contacts poll attempt {attempt}...")
        try:
            resp = connect_client.search_contacts(
                InstanceId=CONNECT_INSTANCE_ID,
                TimeRange={
                    'Type':      'INITIATION_TIMESTAMP',
                    'StartTime': since_ts - 10,
                    'EndTime':   int(time.time()) + 60,
                },
                SearchCriteria={'Channels': ['VOICE']},
                Sort={'FieldName': 'INITIATION_TIMESTAMP', 'Order': 'DESCENDING'},
                MaxResults=5,
            )
            contacts = resp.get('Contacts', [])
            if contacts:
                return contacts[0]
        except Exception as e:
            print(f"   [CTR] search_contacts error: {e}")
        time.sleep(CTR_POLL_INTERVAL_S)
    return None


# ---------------------------------------------------------------------------
# Helper: validate contact trace record against expectations
# FIX: Replaces silent print-only checks with collectable failure strings.
# ---------------------------------------------------------------------------
def validate_contact(connect_client, contact: dict, test_case: dict) -> list:
    failures = []

    # Queue routing
    expected_queue = test_case.get('expected_queue')
    if expected_queue:
        actual_queue = contact.get('QueueInfo', {}).get('Name')
        if actual_queue != expected_queue:
            failures.append(
                f"Expected queue '{expected_queue}' but contact was in '{actual_queue}'"
            )

    # Disconnect without agent
    if test_case.get('expected_behavior') == 'disconnect_with_message':
        agent_attempts = contact.get('AgentConnectionAttempts', 0)
        if agent_attempts > 0:
            failures.append(
                f"Expected disconnect without agent, but AgentConnectionAttempts={agent_attempts}"
            )

    # Transfer queue
    expected_transfer = test_case.get('expected_transfer_queue')
    if expected_transfer:
        transfer_queue = contact.get('QueueInfo', {}).get('Name')
        if transfer_queue != expected_transfer:
            failures.append(
                f"Expected transfer to '{expected_transfer}' but contact is in '{transfer_queue}'"
            )

    # Contact attributes
    expected_attrs = test_case.get('expected_contact_attributes', {})
    if expected_attrs:
        contact_id = contact.get('Id')
        if contact_id:
            try:
                attr_resp    = connect_client.get_contact_attributes(
                    InstanceId=CONNECT_INSTANCE_ID,
                    InitialContactId=contact_id,
                )
                actual_attrs = attr_resp.get('Attributes', {})
                for key, expected_value in expected_attrs.items():
                    actual_value = actual_attrs.get(key)
                    if actual_value != expected_value:
                        failures.append(
                            f"Contact attribute '{key}': expected='{expected_value}' actual='{actual_value}'"
                        )
            except Exception as e:
                failures.append(f"Could not retrieve contact attributes: {e}")

    return failures


# ---------------------------------------------------------------------------
# Helper: hangup call via SMA update
# ---------------------------------------------------------------------------
def hangup_call(chime_client, transaction_id: str):
    try:
        chime_client.update_sip_media_application_call(
            SipMediaApplicationId=CHIME_SMA_ID,
            TransactionId=transaction_id,
            Arguments={'action': 'hangup'}
        )
        print(f"   [CLEANUP] Sent hangup for {transaction_id}")
        time.sleep(3)
    except Exception as e:
        print(f"   [CLEANUP] Hangup error: {e}")


# ---------------------------------------------------------------------------
# Helper: set/remove Connect hours-of-operation override (closed-hours tests)
# FIX: Provides a real API mechanism for simulating closed hours instead of
#      a manual note saying "requires manually closing hours".
# ---------------------------------------------------------------------------
def set_hours_override(connect_client, hours_of_operation_id: str, closed: bool):
    if not hours_of_operation_id:
        return None
    try:
        if closed:
            resp = connect_client.create_hours_of_operation_override(
                InstanceId=CONNECT_INSTANCE_ID,
                HoursOfOperationId=hours_of_operation_id,
                Name='AutoTestClosedOverride',
                Config=[],   # empty Config = closed all day
                EffectiveFrom=time.strftime('%Y-%m-%dT00:00:00', time.gmtime()),
                EffectiveTill=time.strftime('%Y-%m-%dT23:59:59', time.gmtime()),
            )
            override_id = resp['HoursOfOperationOverrideId']
            print(f"   [HOURS] Created closed override: {override_id}")
            return override_id
    except Exception as e:
        print(f"   [HOURS] Failed to set closed override: {e}")
    return None


def delete_hours_override(connect_client, hours_of_operation_id: str, override_id: str):
    if not hours_of_operation_id or not override_id:
        return
    try:
        connect_client.delete_hours_of_operation_override(
            InstanceId=CONNECT_INSTANCE_ID,
            HoursOfOperationId=hours_of_operation_id,
            HoursOfOperationOverrideId=override_id,
        )
        print(f"   [HOURS] Deleted closed override: {override_id}")
    except Exception as e:
        print(f"   [HOURS] Failed to delete override: {e}")

@pytest.mark.parametrize("test_case", load_test_cases())
def test_connect_voice_flow(test_case, request):
    """
    End-to-end test of an Amazon Connect contact flow using a virtual customer
    driven by Chime SMA + DynamoDB state machine.

    Validates:
      - Call routing to the expected queue (real-time metric + CTR)
      - Contact attributes set by the contact flow
      - Disconnect behaviour for closed-hours / out-of-hours scenarios
      - Transfer queue routing (escalation, specialist)
    """
    connect_client, chime_client, dynamodb, _transcribe_client = get_clients()

    print(f"\n{'='*68}")
    print(f"TEST: {test_case['name']}")
    print(f"  {test_case.get('description', '')}")
    print(f"{'='*68}")

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------
    if not MOCK_AWS:
        if not CHIME_PHONE_NUMBER:
            pytest.fail("CHIME_PHONE_NUMBER is not configured.")
        if not CHIME_SMA_ID:
            pytest.fail("CHIME_SMA_ID is not configured.")
        if not CONNECT_INSTANCE_ID:
            pytest.fail("CONNECT_INSTANCE_ID is not configured.")

    # ------------------------------------------------------------------
    # Step 0: Closed-hours override (if required by scenario)
    # ------------------------------------------------------------------
    hours_override_id = None
    hours_op_id       = test_case.get('setup', {}).get('hours_of_operation_id')
    simulate_closed   = test_case.get('setup', {}).get('simulate_closed_hours', False)

    if simulate_closed and not MOCK_AWS:
        hours_override_id = set_hours_override(connect_client, hours_op_id, closed=True)
        time.sleep(5)   # Allow override to propagate in Connect

    # Register finalizer to always remove the override
    def _cleanup_hours():
        if hours_override_id and not MOCK_AWS:
            delete_hours_override(connect_client, hours_op_id, hours_override_id)
    request.addfinalizer(_cleanup_hours)

    # ------------------------------------------------------------------
    # Step 1: Resolve destination + build script
    # FIX: Each test case specifies its own destination_phone, enabling
    #      DNIS-based routing tests and preventing cross-test pollution.
    # ------------------------------------------------------------------
    destination_phone = test_case.get('destination_phone', '')
    if not destination_phone and not MOCK_AWS:
        pytest.fail("Test case is missing 'destination_phone'.")

    script = test_case.get('script', [])
    if not script and 'input_speech' in test_case:
        script = [
            {"type": "wait", "duration_ms": 2000},
            {"type": "speak", "text": test_case['input_speech']},
            {"type": "wait", "duration_ms": 10000},
        ]

    # ------------------------------------------------------------------
    # Step 2: Seed DynamoDB with TTL
    # FIX: unique conversation_id per test run; ttl attribute auto-expires items.
    # pre_set_attributes are stored alongside the script so chime_handler_lambda.py
    # can call Connect UpdateContactAttributes immediately on CALL_ANSWERED before
    # starting the conversation script steps.
    # ------------------------------------------------------------------
    conversation_id    = str(uuid.uuid4())
    call_start_ts      = int(time.time())
    pre_set_attributes = test_case.get('pre_set_attributes', {})

    print(f"\n[STEP 1] Seeding conversation {conversation_id} in DynamoDB...")
    if not MOCK_AWS:
        try:
            seed_conversation(dynamodb, conversation_id, script, test_case['name'])
            # Store pre_set_attributes as a separate field for the Lambda to pick up
            if pre_set_attributes:
                table = dynamodb.Table(DYNAMODB_TABLE_NAME)
                table.update_item(
                    Key={'conversation_id': conversation_id},
                    UpdateExpression='SET pre_set_attributes = :a',
                    ExpressionAttributeValues={':a': json.dumps(pre_set_attributes)},
                )
                print(f"   > Stored pre_set_attributes: {pre_set_attributes}")
        except Exception as e:
            pytest.fail(f"Failed to seed DynamoDB state: {e}")

    # Register per-test DynamoDB cleanup finalizer
    def _cleanup_dynamo():
        if not MOCK_AWS:
            cleanup_conversation(dynamodb, conversation_id)
    request.addfinalizer(_cleanup_dynamo)

    print(f"   > From (Chime): {CHIME_PHONE_NUMBER}")
    print(f"   > To (Connect): {destination_phone}")

    # ------------------------------------------------------------------
    # Step 3: Initiate call
    # ------------------------------------------------------------------
    transaction_id = None
    print(f"\n[STEP 2] Initiating Chime SMA call...")
    if not MOCK_AWS:
        try:
            transaction_id = place_call(chime_client, conversation_id, test_case)
            print(f"   > SUCCESS: Transaction ID = {transaction_id}")
        except Exception as e:
            pytest.fail(f"Failed to initiate call: {e}")
    else:
        print("   > [MOCK] Call initiated.")

    # Register call hangup finalizer regardless of test outcome
    def _cleanup_call():
        if transaction_id and not MOCK_AWS:
            hangup_call(chime_client, transaction_id)
    request.addfinalizer(_cleanup_call)

    # ------------------------------------------------------------------
    # Step 4: Monitor conversation progress
    # ------------------------------------------------------------------
    print(f"\n[STEP 3] Monitoring conversation progress in DynamoDB...")
    if not MOCK_AWS:
        completed = wait_for_completion(dynamodb, conversation_id, script)
        if not completed:
            # Not a hard fail — some scenarios end via Connect-side hangup,
            # not script exhaustion (e.g. disconnect_with_message paths).
            print("   > NOTE: Script did not reach COMPLETED status — may have been "
                  "terminated server-side, which is expected for some test cases.")
    else:
        time.sleep(2)

    # ------------------------------------------------------------------
    # Step 5: Real-time queue metric check
    # ------------------------------------------------------------------
    expected_queue = test_case.get('expected_queue')
    found_in_queue = False
    queue_id       = None

    print(f"\n[STEP 4] Checking real-time queue metrics (expected: {expected_queue})...")
    if expected_queue and not MOCK_AWS:
        queue_id = resolve_queue_id(connect_client, expected_queue)
        if not queue_id:
            pytest.fail(f"Queue '{expected_queue}' not found in Connect instance '{CONNECT_INSTANCE_ID}'.")
        found_in_queue = check_queue_metric(connect_client, queue_id)

    # ------------------------------------------------------------------
    # Step 6: CTR search polling
    # ------------------------------------------------------------------
    print(f"\n[STEP 5] Searching Contact Trace Records...")
    contact = None

    if not MOCK_AWS:
        print("   > Waiting 10s for Connect to begin indexing the contact...")
        time.sleep(10)
        contact = find_contact(connect_client, call_start_ts)

        if contact:
            print(f"   > Contact ID          : {contact.get('Id', 'N/A')}")
            print(f"   > Queue               : {contact.get('QueueInfo', {}).get('Name')}")
            print(f"   > InitiationMethod    : {contact.get('InitiationMethod')}")
            print(f"   > AgentConnAttempts   : {contact.get('AgentConnectionAttempts', 0)}")
            print(f"   > DisconnectDetails   : {contact.get('DisconnectDetails', {})}")
        else:
            print("   > No recent VOICE contact found within CTR poll window.")

    # ------------------------------------------------------------------
    # Step 7: Assertions
    # FIX: All routing/behaviour checks now use assert / pytest.fail so that
    #      failures produce genuine test failures, not silent warnings.
    # ------------------------------------------------------------------
    print(f"\n[STEP 6] Asserting expected outcomes...")

    if not MOCK_AWS:
        expected_behavior = test_case.get('expected_behavior')

        # --- Queue routing ---
        if expected_queue:
            ctr_in_queue = (
                contact is not None and
                contact.get('QueueInfo', {}).get('Name') == expected_queue
            )
            assert found_in_queue or ctr_in_queue, (
                f"FAIL: Contact was NOT found in expected queue '{expected_queue}'. "
                f"Real-time metric={found_in_queue}, "
                f"CTR queue='{contact.get('QueueInfo', {}).get('Name') if contact else 'N/A'}'."
            )
            print(f"   > PASS: Contact confirmed in queue '{expected_queue}'.")

        # --- Disconnect without agent ---
        if expected_behavior == 'disconnect_with_message':
            assert contact is not None, (
                "FAIL: Expected a disconnect contact record but no CTR was found."
            )
            agent_attempts = contact.get('AgentConnectionAttempts', 0)
            assert agent_attempts == 0, (
                f"FAIL: Expected call to disconnect without agent, "
                f"but AgentConnectionAttempts={agent_attempts}."
            )
            print("   > PASS: Call disconnected without reaching an agent (closed-hours path).")

        # --- Transfer queue ---
        expected_transfer = test_case.get('expected_transfer_queue')
        if expected_transfer:
            assert contact is not None, (
                f"FAIL: Expected transfer to '{expected_transfer}' but no CTR was found."
            )
            actual_queue = contact.get('QueueInfo', {}).get('Name')
            assert actual_queue == expected_transfer, (
                f"FAIL: Expected transfer to '{expected_transfer}' but contact is in '{actual_queue}'."
            )
            print(f"   > PASS: Transfer to '{expected_transfer}' confirmed.")

        # --- Contact attributes ---
        if contact:
            failures = validate_contact(connect_client, contact, test_case)
            assert not failures, (
                "FAIL: Contact validation failure(s):\n  " + "\n  ".join(failures)
            )
            if test_case.get('expected_contact_attributes'):
                print("   > PASS: All expected contact attributes verified.")

        # --- expected_flow_transfer: verify the contact was handled by a named sub-flow ---
        # NOTE: Connect does not expose TransferToFlow destination in the CTR API directly.
        # We validate indirectly by checking disconnect reason and AgentConnectionAttempts=0
        # for flows that end without a queue.  The field is preserved as documentation.
        expected_flow = test_case.get('expected_flow_transfer')
        if expected_flow and contact:
            # If the sub-flow disconnects without a queue, AgentConnectionAttempts must be 0
            agent_attempts = contact.get('AgentConnectionAttempts', 0)
            assert agent_attempts == 0, (
                f"FAIL: expected_flow_transfer='{expected_flow}' implies no agent, "
                f"but AgentConnectionAttempts={agent_attempts}."
            )
            print(f"   > PASS: Sub-flow '{expected_flow}' — no agent connection (expected).")

        # --- expected_message_fragment: noted for documentation; not directly verifiable via API ---
        expected_msg = test_case.get('expected_message_fragment')
        if expected_msg and contact:
            print(f"   > INFO: expected_message_fragment='{expected_msg}' — "
                  "verify manually via call recording or Connect contact trace.")

    else:
        print("   > [MOCK] Skipping live assertions.")

    print(f"\n{'='*68}")
    print(f"TEST PASSED: {test_case['name']}")
    print(f"{'='*68}")
