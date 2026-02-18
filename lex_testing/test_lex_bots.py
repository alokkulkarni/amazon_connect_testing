"""
Amazon Lex V2 Bot Regression Tests
====================================
Validates intent recognition, slot filling, dialog state transitions,
session attributes, response messages, and intent redirections.

Test cases are loaded from lex_test_cases.json in the same directory as
this script (lex_testing/lex_test_cases.json).

Supports:
  - Single-turn tests  – flat test case with a single input_text
  - Multi-turn tests   – test cases with a 'turns' array for full conversations
  - Dialog state       – ElicitSlot, ConfirmIntent, Fulfilled, ReadyForFulfillment, Failed, Close
  - Slot elicitation   – validates which slot Lex is currently asking for
  - Slot values        – validates interpreted slot values at any turn
  - Intent state       – InProgress, Fulfilled, ReadyForFulfillment, Failed
  - Session attributes – inject at start; validate at any turn
  - Message fragments  – case-insensitive substring match on bot responses
  - Active contexts    – validate Lex active context names after each turn
  - Fallback           – validates FallbackIntent is triggered for unrecognised input
"""

import boto3
import json
import pytest
import os
import sys
import uuid
import time
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: load .env from this folder (lex_testing/.env) first, then fall
# back to the repo-root .env so that suite-local overrides take precedence.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
load_dotenv(os.path.join(_HERE,      ".env"))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# ---------------------------------------------------------------------------
# Configuration – env vars override; individual test cases override env vars
# ---------------------------------------------------------------------------
DEFAULT_BOT_ID       = os.getenv("LEX_BOT_ID", "")
DEFAULT_BOT_ALIAS_ID = os.getenv("LEX_BOT_ALIAS_ID", "")
DEFAULT_LOCALE_ID    = os.getenv("LEX_LOCALE_ID", "en_US")
AWS_REGION           = os.getenv("AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# Test-cases file: always co-located with this script
# ---------------------------------------------------------------------------
TEST_CASES_FILE = os.path.join(_HERE, "lex_test_cases.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_lex_client():
    """Return a Lex V2 Runtime client, failing the test on credential issues."""
    try:
        return boto3.client("lexv2-runtime", region_name=AWS_REGION)
    except NoCredentialsError:
        pytest.fail("No AWS credentials found. Configure via ~/.aws or environment variables.")
    except Exception as exc:
        pytest.fail(f"Failed to create Lex client: {exc}")


def load_lex_test_cases():
    """Load test cases from lex_test_cases.json (same directory as this file)."""
    if not os.path.exists(TEST_CASES_FILE):
        raise FileNotFoundError(
            f"lex_test_cases.json not found at expected path: {TEST_CASES_FILE}"
        )
    with open(TEST_CASES_FILE, "r") as fh:
        cases = json.load(fh)
    if not cases:
        raise ValueError("lex_test_cases.json is empty – add at least one test case.")
    return cases


def _extract_slot_value(slot_obj):
    """Return the interpreted value string from a Lex slot object, or None."""
    if not slot_obj:
        return None
    # Lex V2 slot shape: {"value": {"interpretedValue": "...", "originalValue": "..."}, ...}
    val = slot_obj.get("value")
    if val:
        return val.get("interpretedValue") or val.get("originalValue")
    # Some shapes nest under "resolvedValues"
    resolved = slot_obj.get("resolvedValues", [])
    return resolved[0] if resolved else None


def _build_session_state(session_attributes: dict | None) -> dict:
    """Construct a sessionState dict for injecting session attributes."""
    if not session_attributes:
        return {}
    return {"sessionAttributes": session_attributes}


def _assert_turn(
    turn_num: int,
    response: dict,
    expected: dict,
    session_id: str,
) -> None:
    """
    Assert all expectations defined in a single turn dict.

    Expected keys (all optional):
      expected_intent        – str  – intent name
      expected_intent_state  – str  – InProgress | Fulfilled | ReadyForFulfillment | Failed
      expected_dialog_state  – str  – ElicitSlot | ConfirmIntent | Fulfilled | Close | …
      expected_elicited_slot – str  – slot name Lex is currently asking for
      expected_slots         – dict – {slotName: interpretedValue} subset to verify
      expected_message_fragment – str – case-insensitive substring of joined bot message
      expected_session_attributes – dict – {key: value} subset to verify
      expected_active_contexts    – list[str] – context names that must be active
    """
    prefix = f"[Turn {turn_num}]"

    session_state = response.get("sessionState", {})
    intent_data   = session_state.get("intent", {})
    dialog_action = session_state.get("dialogAction", {})

    detected_intent = intent_data.get("name")
    intent_state    = intent_data.get("state")
    slots           = intent_data.get("slots") or {}
    dialog_state    = dialog_action.get("type") or session_state.get("dialogAction", {}).get("type")
    elicited_slot   = dialog_action.get("slotToElicit")
    session_attrs   = session_state.get("sessionAttributes") or {}
    active_contexts = [c.get("name") for c in session_state.get("activeContexts") or []]

    messages     = response.get("messages", [])
    message_text = " ".join(m.get("content", "") for m in messages if m.get("content"))

    print(f"   {prefix} Intent: {detected_intent} | State: {intent_state} | Dialog: {dialog_state}")
    print(f"   {prefix} Elicited slot: {elicited_slot}")
    print(f"   {prefix} Slots: {json.dumps({k: _extract_slot_value(v) for k, v in slots.items()}, default=str)}")
    print(f"   {prefix} Message: '{message_text}'")
    if session_attrs:
        print(f"   {prefix} Session attrs: {session_attrs}")
    if active_contexts:
        print(f"   {prefix} Active contexts: {active_contexts}")

    failures = []

    # 1. Intent name
    exp_intent = expected.get("expected_intent")
    if exp_intent is not None:
        if detected_intent != exp_intent:
            failures.append(
                f"{prefix} Intent: expected '{exp_intent}', got '{detected_intent}'"
            )
        else:
            print(f"   {prefix} PASS intent='{exp_intent}'")

    # 2. Intent state
    exp_intent_state = expected.get("expected_intent_state")
    if exp_intent_state is not None:
        if intent_state != exp_intent_state:
            failures.append(
                f"{prefix} IntentState: expected '{exp_intent_state}', got '{intent_state}'"
            )
        else:
            print(f"   {prefix} PASS intent_state='{exp_intent_state}'")

    # 3. Dialog state
    exp_dialog_state = expected.get("expected_dialog_state")
    if exp_dialog_state is not None:
        if dialog_state != exp_dialog_state:
            failures.append(
                f"{prefix} DialogState: expected '{exp_dialog_state}', got '{dialog_state}'"
            )
        else:
            print(f"   {prefix} PASS dialog_state='{exp_dialog_state}'")

    # 4. Elicited slot
    exp_elicited = expected.get("expected_elicited_slot")
    if exp_elicited is not None:
        if elicited_slot != exp_elicited:
            failures.append(
                f"{prefix} ElicitedSlot: expected '{exp_elicited}', got '{elicited_slot}'"
            )
        else:
            print(f"   {prefix} PASS elicited_slot='{exp_elicited}'")

    # 5. Slot values
    exp_slots = expected.get("expected_slots", {})
    for slot_name, exp_val in exp_slots.items():
        actual_val = _extract_slot_value(slots.get(slot_name))
        if actual_val != exp_val:
            failures.append(
                f"{prefix} Slot '{slot_name}': expected '{exp_val}', got '{actual_val}'"
            )
        else:
            print(f"   {prefix} PASS slot '{slot_name}'='{exp_val}'")

    # 6. Message fragment
    exp_fragment = expected.get("expected_message_fragment")
    if exp_fragment is not None:
        if exp_fragment.lower() not in message_text.lower():
            failures.append(
                f"{prefix} Message: fragment '{exp_fragment}' not found in '{message_text}'"
            )
        else:
            print(f"   {prefix} PASS message contains '{exp_fragment}'")

    # 7. Session attributes
    exp_sess_attrs = expected.get("expected_session_attributes", {})
    for attr_key, exp_attr_val in exp_sess_attrs.items():
        actual_attr_val = session_attrs.get(attr_key)
        if actual_attr_val != exp_attr_val:
            failures.append(
                f"{prefix} SessionAttr '{attr_key}': expected '{exp_attr_val}', got '{actual_attr_val}'"
            )
        else:
            print(f"   {prefix} PASS session_attr '{attr_key}'='{exp_attr_val}'")

    # 8. Active contexts
    exp_contexts = expected.get("expected_active_contexts", [])
    for ctx_name in exp_contexts:
        if ctx_name not in active_contexts:
            failures.append(
                f"{prefix} ActiveContext '{ctx_name}' not found. Active: {active_contexts}"
            )
        else:
            print(f"   {prefix} PASS active_context='{ctx_name}'")

    if failures:
        pytest.fail("\n".join(failures))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def lex_client():
    return get_lex_client()


# ---------------------------------------------------------------------------
# Test: single-turn and multi-turn parametrised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("test_case", load_lex_test_cases(), ids=lambda tc: tc.get("name", "unnamed"))
def test_lex_bot(test_case, lex_client):
    """
    Execute a Lex V2 bot test case.

    Single-turn: test case has 'input_text' at the top level.
    Multi-turn:  test case has a 'turns' list; each turn has its own
                 'input_text' and assertions.

    Configuration priority: test_case fields > env vars > defaults.
    """
    bot_id       = test_case.get("bot_id")       or DEFAULT_BOT_ID
    bot_alias_id = test_case.get("bot_alias_id") or DEFAULT_BOT_ALIAS_ID
    locale_id    = test_case.get("locale_id")    or DEFAULT_LOCALE_ID

    if not bot_id or not bot_alias_id:
        pytest.skip(
            f"Skipping '{test_case.get('name')}': "
            "bot_id / bot_alias_id not set in test case or LEX_BOT_ID / LEX_BOT_ALIAS_ID env vars."
        )

    # Unique session per test run – prevents cross-test context leakage
    session_id = f"pytest-{uuid.uuid4().hex[:12]}"

    print(f"\n{'='*68}")
    print(f"LEX TEST: {test_case.get('name', 'Unnamed')}")
    print(f"  {test_case.get('description', '')}")
    print(f"  Bot: {bot_id}  Alias: {bot_alias_id}  Locale: {locale_id}")
    print(f"  Session: {session_id}")
    print(f"{'='*68}")

    # -----------------------------------------------------------------------
    # Build the initial session state (inject session attributes if provided)
    # -----------------------------------------------------------------------
    initial_session_attrs = test_case.get("initial_session_attributes", {})

    def _call_lex(input_text: str, extra_session_state: dict | None = None) -> dict:
        """Invoke recognize_text, merging caller-supplied session state."""
        kwargs: dict = {
            "botId":       bot_id,
            "botAliasId":  bot_alias_id,
            "localeId":    locale_id,
            "sessionId":   session_id,
            "text":        input_text,
        }
        if extra_session_state:
            kwargs["sessionState"] = extra_session_state
        try:
            return lex_client.recognize_text(**kwargs)
        except ClientError as exc:
            pytest.fail(f"Lex API error on input '{input_text}': {exc}")

    # -----------------------------------------------------------------------
    # Determine whether this is a multi-turn or single-turn test
    # -----------------------------------------------------------------------
    turns = test_case.get("turns")

    if turns:
        # ---- Multi-turn conversation ----
        print(f"\n[MODE] Multi-turn ({len(turns)} turns)")
        for turn_num, turn in enumerate(turns, start=1):
            input_text = turn.get("input_text")
            if not input_text:
                pytest.fail(f"Turn {turn_num} is missing 'input_text'.")

            print(f"\n--- Turn {turn_num}: '{input_text}' ---")

            # Only inject session attributes on the first turn
            extra_ss = _build_session_state(initial_session_attrs) if turn_num == 1 and initial_session_attrs else None
            response  = _call_lex(input_text, extra_ss)

            _assert_turn(turn_num, response, turn, session_id)

            # Optional inter-turn delay (e.g. to avoid throttling)
            delay_ms = turn.get("delay_ms", 0)
            if delay_ms:
                time.sleep(delay_ms / 1000)

    else:
        # ---- Single-turn ----
        input_text = test_case.get("input_text")
        if not input_text:
            pytest.fail("Test case is missing 'input_text' (and has no 'turns' list).")

        print(f"\n[MODE] Single-turn: '{input_text}'")
        extra_ss = _build_session_state(initial_session_attrs) if initial_session_attrs else None
        response  = _call_lex(input_text, extra_ss)
        _assert_turn(1, response, test_case, session_id)

    print(f"\n{'='*68}")
    print(f"PASSED: {test_case.get('name', 'Unnamed')}")
    print(f"{'='*68}")


if __name__ == "__main__":
    sys.exit(pytest.main(["-s", "-v", __file__]))
