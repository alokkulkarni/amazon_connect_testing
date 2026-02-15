import boto3
import json
import pytest
import os
import sys
import uuid
import time
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
# These can be overridden by individual test cases in test_cases.json
# If running from a subdirectory, we might need to look up for .env
# But typically .env is in the root of the project.

DEFAULT_BOT_ID = os.getenv("LEX_BOT_ID")
DEFAULT_BOT_ALIAS_ID = os.getenv("LEX_BOT_ALIAS_ID")
DEFAULT_LOCALE_ID = os.getenv("LEX_LOCALE_ID", "en_US")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

def get_lex_client():
    """Initialize the Lex V2 Runtime client."""
    try:
        return boto3.client("lexv2-runtime", region_name=AWS_REGION)
    except NoCredentialsError:
        pytest.fail("No AWS credentials found. Please configure your credentials.")
    except Exception as e:
        pytest.fail(f"Failed to create Lex client: {e}")

def load_lex_test_cases():
    """Load test cases from JSON file."""
    # Assume lex_test_cases.json is in the parent directory or current directory
    possible_paths = ["lex_test_cases.json", "../lex_test_cases.json", "../../lex_test_cases.json"]
    test_cases_file = None
    for path in possible_paths:
        if os.path.exists(path):
            test_cases_file = path
            break
            
    if not test_cases_file:
        print("WARNING: lex_test_cases.json not found in search paths.")
        return []
    
    try:
        with open(test_cases_file, "r") as f:
            all_cases = json.load(f)
            # No need to filter by type if the file only contains Lex tests
            return all_cases
    except Exception as e:
        print(f"Error loading test cases: {e}")
        return []

@pytest.mark.parametrize("test_case", load_lex_test_cases())
def test_lex_bot_conversation(test_case):
    """
    Executes a Lex V2 bot conversation test.
    Validates intent recognition, slot filling, and response messages.
    """
    lex_client = get_lex_client()
    
    print(f"\n----------------------------------------------------------------")
    print(f"STARTING LEX TEST: {test_case.get('name', 'Unnamed')}")
    print(f"----------------------------------------------------------------")
    
    # Resolve Bot Details (TestCase > Env Var > Fail)
    bot_id = test_case.get('bot_id') or DEFAULT_BOT_ID
    bot_alias_id = test_case.get('bot_alias_id') or DEFAULT_BOT_ALIAS_ID
    locale_id = test_case.get('locale_id') or DEFAULT_LOCALE_ID

    if not bot_id or not bot_alias_id:
        pytest.skip("Skipping: Missing Bot ID or Alias ID in test case or environment.")

    # Generate a unique session ID for each test execution to avoid context pollution
    session_id = f"test-{uuid.uuid4().hex[:10]}"
    input_text = test_case.get('input_text')
    
    if not input_text:
        pytest.fail("Test case missing 'input_text'")

    print(f"[STEP 1] Setup: Sending text to Lex Bot...")
    print(f"   > Bot ID: {bot_id}")
    print(f"   > Alias ID: {bot_alias_id}")
    print(f"   > Input: '{input_text}'")

    try:
        # Call Lex V2 RecognizeText API
        response = lex_client.recognize_text(
            botId=bot_id,
            botAliasId=bot_alias_id,
            localeId=locale_id,
            sessionId=session_id,
            text=input_text
        )
        
        # Parse Response
        session_state = response.get('sessionState', {})
        intent_data = session_state.get('intent', {})
        detected_intent = intent_data.get('name')
        slots = intent_data.get('slots', {}) or {}
        
        messages = response.get('messages', [])
        message_texts = [m.get('content', '') for m in messages if m.get('content')]
        full_message = " ".join(message_texts)

        print(f"[STEP 2] Response Received:")
        print(f"   > Detected Intent: {detected_intent}")
        print(f"   > Slots: {json.dumps(slots, default=str)}")
        print(f"   > Message: '{full_message}'")

        # Validation
        print(f"[STEP 3] Validation Results")
        
        # 1. Validate Intent
        expected_intent = test_case.get('expected_intent')
        if expected_intent:
            if detected_intent == expected_intent:
                print(f"   > PASS: Intent matched '{expected_intent}'")
            else:
                pytest.fail(f"Intent mismatch. Expected '{expected_intent}', got '{detected_intent}'")
            
        # 2. Validate Slots
        expected_slots = test_case.get('expected_slots', {})
        for slot_key, expected_val in expected_slots.items():
            actual_slot = slots.get(slot_key)
            actual_val = None
            if actual_slot:
                actual_val = actual_slot.get('value', {}).get('interpretedValue')
            
            if actual_val == expected_val:
                print(f"   > PASS: Slot '{slot_key}' matched '{expected_val}'")
            else:
                pytest.fail(f"Slot '{slot_key}' mismatch. Expected '{expected_val}', got '{actual_val}'")

        # 3. Validate Message
        expected_fragment = test_case.get('expected_message_fragment')
        if expected_fragment:
            if expected_fragment.lower() in full_message.lower():
                print(f"   > PASS: Message contained '{expected_fragment}'")
            else:
                pytest.fail(f"Message mismatch. Expected fragment '{expected_fragment}' not found in '{full_message}'")

    except ClientError as e:
        print(f"   > ERROR: AWS Client failed: {e}")
        pytest.fail(f"AWS ClientError: {e}")
    except AssertionError as ae:
        print(f"   > FAIL: {ae}")
        raise ae
    except Exception as e:
        print(f"   > CRITICAL: Unexpected error: {e}")
        pytest.fail(f"Unexpected Error: {e}")

if __name__ == "__main__":
    # If run directly, execute pytest on this file
    sys.exit(pytest.main(["-s", "-v", __file__]))
