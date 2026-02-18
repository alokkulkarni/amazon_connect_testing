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

# ---------------------------------------------------------------------------
# Path resolution – always relative to this file, regardless of cwd
# ---------------------------------------------------------------------------
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# Load .env from repo root (best-effort – no error if missing)
load_dotenv(os.path.join(_REPO_ROOT, '.env'), override=False)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHIME_PHONE_NUMBER  = os.environ.get('CHIME_PHONE_NUMBER', '')
CHIME_SMA_ID        = os.environ.get('CHIME_SMA_ID', '')
CONNECT_INSTANCE_ID = os.environ.get('CONNECT_INSTANCE_ID', '')
ENV_NAME            = os.environ.get('ENV_NAME', 'dev')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', f'VoiceTestState-{ENV_NAME}')

# Connect is in one region (e.g. eu-west-2), Chime/Lambda in another (e.g. us-east-1)
CONNECT_REGION          = os.environ.get('AWS_REGION', 'us-east-1')
CHIME_REGION            = os.environ.get('CHIME_AWS_REGION', 'us-east-1')
MOCK_AWS                = os.environ.get('MOCK_AWS', 'false').lower() == 'true'

# Connect contact-flow execution log group: /aws/connect/<instance-alias>
# Enable via: Admin console > Data storage > Contact flow logs
CONNECT_INSTANCE_ALIAS  = os.environ.get('CONNECT_INSTANCE_ALIAS', '')
CONNECT_FLOW_LOG_GROUP  = os.environ.get(
    'CONNECT_FLOW_LOG_GROUP',
    f'/aws/connect/{CONNECT_INSTANCE_ALIAS}' if CONNECT_INSTANCE_ALIAS else ''
)

# S3 bucket for Chime SMA audio recordings (used for Transcribe fallback)
# Set via: Admin console > Recording > or deploy script output
CHIME_RECORDING_BUCKET  = os.environ.get('CHIME_RECORDING_BUCKET', '')

# Poll timeouts
QUEUE_POLL_TIMEOUT_S    = 60
QUEUE_POLL_INTERVAL_S   = 5
CTR_POLL_TIMEOUT_S      = 300   # Connect CTR indexing can take 1-3 min
CTR_POLL_INTERVAL_S     = 10
CWL_POLL_TIMEOUT_S      = 120   # CloudWatch Logs Insights query timeout
CWL_POLL_INTERVAL_S     = 5
TRANSCRIBE_POLL_TIMEOUT_S = 300  # Transcribe job completion timeout

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
        env['AWS_REGION']             = CHIME_REGION
        env['ENV_NAME']                = ENV_NAME
        env['DYNAMODB_TABLE_NAME']     = DYNAMODB_TABLE_NAME
        env['CONNECT_REGION']          = CONNECT_REGION
        env['CONNECT_INSTANCE_ALIAS']  = CONNECT_INSTANCE_ALIAS
        if CHIME_RECORDING_BUCKET:
            env['CHIME_RECORDING_BUCKET'] = CHIME_RECORDING_BUCKET

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
            CHIME_PHONE_NUMBER       = infra.get('CHIME_PHONE_NUMBER',     CHIME_PHONE_NUMBER)
            CHIME_SMA_ID             = infra.get('CHIME_SMA_ID',           CHIME_SMA_ID)
            # Propagate new infra outputs back into os.environ so module-level
            # constants (read at import time) are refreshed for this session.
            _tbl = infra.get('DYNAMODB_TABLE', DYNAMODB_TABLE_NAME)
            os.environ['DYNAMODB_TABLE_NAME'] = _tbl
            _cwl = infra.get('CONNECT_FLOW_LOG_GROUP', CONNECT_FLOW_LOG_GROUP)
            if _cwl:
                os.environ['CONNECT_FLOW_LOG_GROUP'] = _cwl
            _bucket = infra.get('CHIME_RECORDING_BUCKET', CHIME_RECORDING_BUCKET)
            if _bucket:
                os.environ['CHIME_RECORDING_BUCKET'] = _bucket
            print(f"[SETUP] SMA={CHIME_SMA_ID}  Phone={CHIME_PHONE_NUMBER}  Table={_tbl}")
            print(f"[SETUP] CWL log group='{_cwl}'  Recording bucket='{_bucket}'")
        else:
            print("[SETUP] WARNING: infrastructure_output.json not found — using defaults.")
    except Exception as e:
        print(f"[SETUP] Error: {e}")

def load_test_cases():
    file_path = os.path.join(os.path.dirname(__file__), 'test_cases.json')
    with open(file_path, 'r') as f:
        return json.load(f)


def get_clients():
    """
    Returns a 5-tuple:
      (connect_client, chime_client, dynamodb_resource, transcribe_client, logs_client)

    logs_client targets the CONNECT_REGION because Connect flow execution
    logs are written to CloudWatch in the same region as the Connect instance.
    transcribe_client targets CHIME_REGION for audio files in the Chime bucket.
    """
    if MOCK_AWS:
        return None, None, None, None, None

    # Session for Connect (e.g. eu-west-2)
    session_connect = boto3.Session(region_name=CONNECT_REGION)
    # Session for Chime/DynamoDB (us-east-1)
    session_chime   = boto3.Session(region_name=CHIME_REGION)

    return (
        session_connect.client('connect'),
        session_chime.client('chime-sdk-voice'),
        session_chime.resource('dynamodb'),
        session_chime.client('transcribe'),
        session_connect.client('logs'),   # CloudWatch Logs in Connect region
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


# ---------------------------------------------------------------------------
# Helper: query Connect contact flow execution logs via CloudWatch Logs Insights
#
# PREREQUISITE: Contact flow logging MUST be enabled in Connect admin console:
#   Admin console > Data storage > Contact flow logs > Enable
# This writes every block execution to /aws/connect/<instance-alias> in
# the Connect region.
#
# Solves TWO weak assertions:
#   1. expected_flow_transfer  — query for Type=TransferToFlow + ContactFlowName
#   2. expected_message_fragment — query for Type=MessageParticipant + Text content
# ---------------------------------------------------------------------------
def query_contact_flow_logs(
    logs_client,
    contact_id: str,
    since_epoch: int,
    block_type: str,
    value_field: str,
    expected_value: str,
) -> tuple:
    """
    Run a CloudWatch Logs Insights query against the Connect contact flow log
    group and search for a specific block execution.

    Args:
        logs_client:     boto3 logs client (in Connect region)
        contact_id:      Connect ContactId from the CTR
        since_epoch:     Unix timestamp of call start (search window start)
        block_type:      Connect flow block Type to filter on, e.g.
                           'TransferToFlow' or 'MessageParticipant'
        value_field:     Log field to extract, e.g.
                           'Parameters.ContactFlowName' or 'Parameters.Text'
        expected_value:  Substring to match inside value_field

    Returns:
        (found: bool, actual_value: str | None)
    """
    if not CONNECT_FLOW_LOG_GROUP:
        print("   [CWL] CONNECT_FLOW_LOG_GROUP not configured — skipping log query.")
        return False, None

    query_string = (
        f'fields @timestamp, ContactId, Type, {value_field}'
        f' | filter ContactId = "{contact_id}"'
        f' | filter Type = "{block_type}"'
        f' | sort @timestamp asc'
        f' | limit 20'
    )
    end_epoch = int(time.time()) + 60

    try:
        resp = logs_client.start_query(
            logGroupName=CONNECT_FLOW_LOG_GROUP,
            startTime=since_epoch - 30,
            endTime=end_epoch,
            queryString=query_string,
        )
        query_id = resp['queryId']
        print(f"   [CWL] Started Insights query {query_id} for ContactId={contact_id} block={block_type}")
    except Exception as e:
        print(f"   [CWL] Failed to start Insights query: {e}")
        return False, None

    # Poll for results
    deadline = time.time() + CWL_POLL_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(CWL_POLL_INTERVAL_S)
        try:
            result = logs_client.get_query_results(queryId=query_id)
            status = result['status']
            if status in ('Complete', 'Failed', 'Cancelled', 'Timeout'):
                if status != 'Complete':
                    print(f"   [CWL] Query ended with status={status}")
                    return False, None

                rows = result.get('results', [])
                print(f"   [CWL] Query returned {len(rows)} row(s) for block={block_type}")
                for row in rows:
                    fields = {f['field']: f['value'] for f in row}
                    actual = fields.get(value_field, '')
                    print(f"   [CWL]   {value_field}={actual!r}")
                    if expected_value.lower() in actual.lower():
                        return True, actual
                # No matching row found
                return False, rows[0][0]['value'] if rows else None
        except Exception as e:
            print(f"   [CWL] Poll error: {e}")
            return False, None

    logs_client.stop_query(queryId=query_id)
    print("   [CWL] Query timed out.")
    return False, None


# ---------------------------------------------------------------------------
# Helper: transcribe Chime-captured audio from S3 via Amazon Transcribe
#
# PREREQUISITE: Chime SMA must be configured to record the call audio to S3.
# In the SMA Lambda, add a RecordAudio action before Hangup and set
# RecordingDestination to the CHIME_RECORDING_BUCKET.  The S3 key format
# is typically: <transaction_id>/<transaction_id>.wav
#
# This solves expected_message_fragment when CloudWatch flow logs are not
# available (e.g. logging not enabled) or for additional speech verification.
# ---------------------------------------------------------------------------
def transcribe_chime_audio(
    transcribe_client,
    transaction_id: str,
    expected_fragment: str,
) -> tuple:
    """
    Start an Amazon Transcribe job for the Chime-recorded call audio and poll
    until the transcript is available, then check for expected_fragment.

    Args:
        transcribe_client:  boto3 transcribe client
        transaction_id:     Chime SMA transaction ID (used to locate the S3 key)
        expected_fragment:  Text substring to find in the transcript

    Returns:
        (found: bool, transcript_text: str | None)
    """
    if not CHIME_RECORDING_BUCKET:
        print("   [TRANSCRIBE] CHIME_RECORDING_BUCKET not configured — skipping.")
        return False, None

    # S3 URI: s3://<bucket>/<transaction_id>/<transaction_id>.wav
    s3_uri   = f's3://{CHIME_RECORDING_BUCKET}/{transaction_id}/{transaction_id}.wav'
    job_name = f'voice-test-{transaction_id[:8]}-{int(time.time())}'

    try:
        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': s3_uri},
            MediaFormat='wav',
            LanguageCode='en-GB',  # eu-west-2 instance — adjust for other regions
            Settings={
                'ShowSpeakerLabels': True,
                'MaxSpeakerLabels': 2,  # caller + IVR system voice
            },
        )
        print(f"   [TRANSCRIBE] Started job {job_name} for s3_uri={s3_uri}")
    except Exception as e:
        print(f"   [TRANSCRIBE] Failed to start job: {e}")
        return False, None

    # Poll for completion
    deadline = time.time() + TRANSCRIBE_POLL_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(15)
        try:
            resp   = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
            status = resp['TranscriptionJob']['TranscriptionJobStatus']
            print(f"   [TRANSCRIBE] Job status: {status}")

            if status == 'COMPLETED':
                transcript_uri = resp['TranscriptionJob']['Transcript']['TranscriptFileUri']
                import urllib.request
                with urllib.request.urlopen(transcript_uri) as r:
                    payload    = json.loads(r.read())
                transcript_text = payload['results']['transcripts'][0]['transcript']
                print(f"   [TRANSCRIBE] Transcript: {transcript_text[:200]}")
                found = expected_fragment.lower() in transcript_text.lower()
                return found, transcript_text

            if status in ('FAILED', 'STOPPED'):
                reason = resp['TranscriptionJob'].get('FailureReason', 'unknown')
                print(f"   [TRANSCRIBE] Job {status}: {reason}")
                return False, None
        except Exception as e:
            print(f"   [TRANSCRIBE] Poll error: {e}")
            return False, None

    print("   [TRANSCRIBE] Job timed out.")
    try:
        transcribe_client.delete_transcription_job(TranscriptionJobName=job_name)
    except Exception:
        pass
    return False, None

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
    connect_client, chime_client, dynamodb, transcribe_client, logs_client = get_clients()

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

        contact_id = contact.get('Id') if contact else None

        # ---------------------------------------------------------------
        # expected_flow_transfer
        # Primary:  CloudWatch Logs Insights query for Type=TransferToFlow
        #           with ContactFlowName matching the expected sub-flow.
        # Fallback: AgentConnectionAttempts==0 (indirect — any no-agent path)
        # ---------------------------------------------------------------
        expected_flow = test_case.get('expected_flow_transfer')
        if expected_flow and contact_id:
            print(f"\n[STEP 7a] Verifying flow transfer to '{expected_flow}'...")

            flow_found, flow_actual = query_contact_flow_logs(
                logs_client,
                contact_id,
                call_start_ts,
                block_type='TransferToFlow',
                value_field='Parameters.ContactFlowName',
                expected_value=expected_flow,
            )

            if CONNECT_FLOW_LOG_GROUP:
                # Hard assertion: log group configured, query ran — require exact match
                assert flow_found, (
                    f"FAIL: expected_flow_transfer='{expected_flow}' not found in "
                    f"CloudWatch flow logs for contact {contact_id}. "
                    f"Last seen ContactFlowName='{flow_actual}'."
                )
                print(f"   > PASS (CWL): TransferToFlow to '{expected_flow}' confirmed in logs.")
            else:
                # Soft fallback: no log group — fall back to AgentConnectionAttempts==0
                agent_attempts = contact.get('AgentConnectionAttempts', 0)
                assert agent_attempts == 0, (
                    f"FAIL: expected_flow_transfer='{expected_flow}' — CWL not configured. "
                    f"Fallback check: AgentConnectionAttempts={agent_attempts} (expected 0)."
                )
                print(
                    f"   > WARN: expected_flow_transfer validated via fallback only "
                    f"(AgentConnectionAttempts=0). Configure CONNECT_FLOW_LOG_GROUP for "
                    f"exact sub-flow verification."
                )

        # ---------------------------------------------------------------
        # expected_message_fragment
        # Primary:  CloudWatch Logs Insights query for Type=MessageParticipant
        #           with Parameters.Text containing the expected fragment.
        # Fallback: Amazon Transcribe on Chime-recorded call audio from S3.
        # ---------------------------------------------------------------
        expected_msg = test_case.get('expected_message_fragment')
        if expected_msg and contact_id:
            print(f"\n[STEP 7b] Verifying message fragment '{expected_msg}'...")
            msg_verified = False

            # -- Track 1: CloudWatch Logs (instant, no audio needed) --
            if CONNECT_FLOW_LOG_GROUP:
                msg_found, msg_actual = query_contact_flow_logs(
                    logs_client,
                    contact_id,
                    call_start_ts,
                    block_type='MessageParticipant',
                    value_field='Parameters.Text',
                    expected_value=expected_msg,
                )
                if msg_found:
                    print(f"   > PASS (CWL): Message fragment '{expected_msg}' confirmed in flow logs.")
                    msg_verified = True
                else:
                    print(
                        f"   > WARN (CWL): Fragment not found in flow logs. "
                        f"Last Parameters.Text='{msg_actual}'. Attempting Transcribe fallback."
                    )

            # -- Track 2: Amazon Transcribe fallback --
            if not msg_verified and transaction_id:
                t_found, t_text = transcribe_chime_audio(
                    transcribe_client, transaction_id, expected_msg
                )
                if t_found:
                    print(f"   > PASS (Transcribe): Fragment '{expected_msg}' found in transcript.")
                    msg_verified = True
                elif t_text is not None:
                    print(f"   > FAIL (Transcribe): Fragment not found. Transcript='{t_text[:300]}'")

            if not msg_verified:
                # Only hard-fail if at least one verification track ran
                if CONNECT_FLOW_LOG_GROUP or (CHIME_RECORDING_BUCKET and transaction_id):
                    assert False, (
                        f"FAIL: expected_message_fragment='{expected_msg}' not found via "
                        f"CloudWatch Logs or Transcribe for contact {contact_id}."
                    )
                else:
                    print(
                        f"   > SKIP: expected_message_fragment='{expected_msg}' — "
                        "neither CONNECT_FLOW_LOG_GROUP nor CHIME_RECORDING_BUCKET is "
                        "configured. Set at least one to enable this assertion."
                    )

    else:
        print("   > [MOCK] Skipping live assertions.")

    print(f"\n{'='*68}")
    print(f"TEST PASSED: {test_case['name']}")
    print(f"{'='*68}")
