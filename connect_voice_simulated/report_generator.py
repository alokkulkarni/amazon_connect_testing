#!/usr/bin/env python3
"""
report_generator.py
===================
Amazon Connect Dashboard Test Runner – Report Generator
--------------------------------------------------------
Responsible for converting the structured JSON run-payload (produced by
run_connect_tests.py) into:

  1. A rich **HTML** report with:
       • An executive summary table (pass/fail/skip/timeout counts + pass-rate)
       • A per-suite breakdown table
       • A per-test-case expandable detail section that includes:
           – Step-by-step execution table
           – Transcript at each step rendered in a chat-style table
       • Colour-coded status badges (green / red / amber / grey)
       • Embedded CSS – single self-contained file, no external dependencies

  2. A **JUnit-compatible XML** report suitable for GitHub Actions test
     reporter annotations, test-results dashboards and standard CI tooling.

Both writers accept the same ``run_payload`` dict produced by
``run_connect_tests.py::run_all_tests()``.

Public API
----------
  write_html_report(run_payload, output_path, report_cfg)
  write_xml_report(run_payload, output_path)
"""

from __future__ import annotations

import html as _html_mod
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("report_generator")


# ===========================================================================
# HTML Report
# ===========================================================================

# ---------------------------------------------------------------------------
# CSS – embedded directly so the HTML file is fully self-contained.
# Theme colours are injected at render-time from report_cfg["html_theme"].
# ---------------------------------------------------------------------------

_CSS_TEMPLATE = """
/* ── Reset & base ─────────────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  font-size: 14px;
  background: #f4f6f8;
  color: #222;
  line-height: 1.5;
}}
h1, h2, h3 {{ font-weight: 600; }}
a {{ color: {accent}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* ── Layout ────────────────────────────────────────────── */
.page-wrap  {{ max-width: 1300px; margin: 0 auto; padding: 20px 24px; }}
.page-header {{
  background: {primary};
  color: #fff;
  padding: 24px 28px;
  border-radius: 8px 8px 0 0;
  display: flex;
  align-items: center;
  gap: 16px;
}}
.page-header h1 {{ font-size: 1.5rem; }}
.page-header .sub {{ font-size: 0.85rem; opacity: .75; margin-top: 4px; }}
.page-header .logo {{
  font-size: 2rem;
  background: {accent};
  border-radius: 50%;
  width: 48px; height: 48px;
  display: flex; align-items: center; justify-content: center;
}}

/* ── Cards ─────────────────────────────────────────────── */
.card {{
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 4px rgba(0,0,0,.12);
  margin-bottom: 24px;
  overflow: hidden;
}}
.card-header {{
  background: {primary};
  color: #fff;
  padding: 12px 20px;
  font-weight: 600;
  font-size: 1rem;
}}

/* ── Stat bar ───────────────────────────────────────────── */
.stat-grid {{
  display: flex;
  gap: 1px;
  background: #dde;
  border-radius: 6px;
  overflow: hidden;
  margin: 20px 0;
}}
.stat-item {{
  flex: 1;
  padding: 18px 24px;
  background: #fff;
  text-align: center;
}}
.stat-item .value {{ font-size: 2rem; font-weight: 700; }}
.stat-item .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: .8px; }}
.stat-pass   .value {{ color: {pass_c}; }}
.stat-fail   .value {{ color: {fail_c}; }}
.stat-warn   .value {{ color: {warn_c}; }}
.stat-total  .value {{ color: {primary}; }}

/* ── Progress bar ───────────────────────────────────────── */
.progress-wrap {{ padding: 0 20px 20px; }}
.progress-bar  {{ height: 10px; background: #e0e0e0; border-radius: 5px; overflow: hidden; }}
.progress-fill {{
  height: 100%; border-radius: 5px;
  background: linear-gradient(90deg, {pass_c} 0%, {accent} 100%);
  transition: width .5s ease;
}}

/* ── Tables ─────────────────────────────────────────────── */
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}}
th {{
  background: {primary}; color: #fff;
  padding: 10px 14px;
  text-align: left;
  font-weight: 500;
  position: sticky; top: 0; z-index: 2;
}}
td {{ padding: 9px 14px; border-bottom: 1px solid #eee; vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: #f8f9fa; }}

/* ── Status badges ──────────────────────────────────────── */
.badge {{
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 0.78rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .6px;
}}
.badge-pass    {{ background: {pass_c}22;  color: {pass_c};  border: 1px solid {pass_c}55; }}
.badge-fail    {{ background: {fail_c}22;  color: {fail_c};  border: 1px solid {fail_c}55; }}
.badge-error   {{ background: {fail_c}22;  color: {fail_c};  border: 1px solid {fail_c}55; }}
.badge-timeout {{ background: {warn_c}22;  color: {warn_c};  border: 1px solid {warn_c}55; }}
.badge-skip    {{ background: #9e9e9e22; color: #666;    border: 1px solid #9e9e9e55; }}
.badge-unknown {{ background: #9e9e9e22; color: #666;    border: 1px solid #9e9e9e55; }}

/* ── Collapsible sections ───────────────────────────────── */
details {{ margin: 4px 0; }}
details summary {{
  cursor: pointer;
  padding: 8px 14px;
  background: #f0f3f5;
  border-radius: 4px;
  font-weight: 500;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
}}
details summary::-webkit-details-marker {{ display: none; }}
details summary::before {{ content: '▶'; font-size: .7rem; transition: transform .2s; }}
details[open] summary::before {{ transform: rotate(90deg); }}
details .inner {{ padding: 12px 14px; background: #fafbfc; border-radius: 0 0 4px 4px; }}

/* ── Transcript chat bubbles ────────────────────────────── */
.transcript-table td.msg-system  {{ color: #888; font-style: italic; }}
.msg-agent     {{ background: {accent}11; }}
.msg-customer  {{ background: {pass_c}11; }}

/* ── Footer ─────────────────────────────────────────────── */
.page-footer {{
  text-align: center;
  color: #888;
  font-size: 0.8rem;
  padding: 16px 0 8px;
}}
"""


def _badge(status: str | None) -> str:
    """Return an HTML badge span for *status*."""
    s    = (status or "UNKNOWN").upper()
    cls  = {
        "PASS":      "badge-pass",
        "FAIL":      "badge-fail",
        "ERROR":     "badge-error",
        "TIMEOUT":   "badge-timeout",
        "SKIPPED":   "badge-skip",
        "CANCELLED": "badge-skip",
    }.get(s, "badge-unknown")
    return f'<span class="badge {cls}">{_esc(s)}</span>'


def _esc(text: Any) -> str:
    """HTML-escape *text*, returning an empty string for None."""
    if text is None:
        return ""
    return _html_mod.escape(str(text))


def _fmt_ts(ts_str: str | None) -> str:
    """Format an ISO-8601 timestamp string for display, or return '–'."""
    if not ts_str:
        return "–"
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return _esc(ts_str)


def _duration(ms: int | float | None) -> str:
    """Format a millisecond duration as a human-readable string."""
    if ms is None:
        return "–"
    ms = int(ms)
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes = seconds / 60
    return f"{minutes:.1f} min"


# ---------------------------------------------------------------------------
# HTML section builders
# ---------------------------------------------------------------------------

def _count_transcripts(steps: list[dict]) -> int:
    """Count extractable transcript rows from Connect Testing observation records."""
    count = 0
    for step in steps:
        rec = step.get("record") or {}
        rec_type = rec.get("Type", "")
        if rec_type in ("INITIATION", "EXECUTION_START", "COMPLETION"):
            count += 1
        elif rec_type == "OBSERVATION":
            if (rec.get("Event") or {}).get("Type"):
                count += 1
            for action in (rec.get("Actions") or []):
                if action.get("Type") == "SendInstruction":
                    count += 1
    return count


def _build_transcript_table(steps: list[dict]) -> str:
    """
    Build a chat-style transcript table from the Connect Testing record
    structure returned by ListTestCaseExecutionRecords.

    Extracts:
      - INITIATION  : call-setup details (source/destination numbers)
      - EXECUTION_START : execution start (ContactId)
      - OBSERVATION  : system event (IVR prompt, flow action) + customer
                        actions (DTMF, message)
      - COMPLETION   : completion reason and observation summary
    """
    rows: list[str] = []
    for step in steps:
        rec = step.get("record") or {}
        rec_type = rec.get("Type", "")
        step_label = rec.get("Identifier") or rec_type
        step_name = _esc(step_label)
        ts = _fmt_ts(step.get("timestamp"))

        if rec_type == "INITIATION":
            ep = rec.get("EntryPoint") or {}
            params = ep.get("Parameters") or {}
            src = _esc(params.get("SourcePhoneNumber", ""))
            dst = _esc(params.get("DestinationPhoneNumber", ""))
            rows.append(
                f"<tr class='msg-system'>"
                f"<td>{step_name}</td>"
                f"<td><strong>SYSTEM</strong></td>"
                f"<td>Call initiated: {src} \u2192 {dst}</td>"
                f"<td style='white-space:nowrap'>{ts}</td>"
                f"</tr>"
            )

        elif rec_type == "EXECUTION_START":
            contact_id = _esc((rec.get("ContactId") or "")[-12:])
            rows.append(
                f"<tr class='msg-system'>"
                f"<td>{step_name}</td>"
                f"<td><strong>SYSTEM</strong></td>"
                f"<td>Test execution started (ContactId: \u2026{contact_id})</td>"
                f"<td style='white-space:nowrap'>{ts}</td>"
                f"</tr>"
            )

        elif rec_type == "OBSERVATION":
            event = rec.get("Event") or {}
            event_type = event.get("Type", "")
            event_props = event.get("Properties") or {}
            event_ts = _fmt_ts(step.get("timestamp"))

            # ── System / IVR event row ─────────────────────────────────
            if event_type == "MessageReceived" and event_props.get("Text"):
                rows.append(
                    f"<tr class='msg-system'>"
                    f"<td>{step_name}</td>"
                    f"<td><strong>IVR / AGENT</strong></td>"
                    f"<td>{_esc(event_props['Text'])}</td>"
                    f"<td style='white-space:nowrap'>{event_ts}</td>"
                    f"</tr>"
                )
            elif event_type == "FlowActionStarted" and event_props.get("ActionType"):
                action_type = event_props["ActionType"]
                action_params = event_props.get("ActionParameters") or {}
                detail_parts = [f"Flow: {action_type}"]
                if action_params.get("QueueId"):
                    qid = action_params["QueueId"].split("/")[-1]
                    detail_parts.append(f"Queue: {qid}")
                rows.append(
                    f"<tr class='msg-system'>"
                    f"<td>{step_name}</td>"
                    f"<td><strong>FLOW</strong></td>"
                    f"<td>{_esc(' | '.join(detail_parts))}</td>"
                    f"<td style='white-space:nowrap'>{event_ts}</td>"
                    f"</tr>"
                )
            elif event_type:
                rows.append(
                    f"<tr class='msg-system'>"
                    f"<td>{step_name}</td>"
                    f"<td><strong>SYSTEM</strong></td>"
                    f"<td>{_esc(event_type)}</td>"
                    f"<td style='white-space:nowrap'>{event_ts}</td>"
                    f"</tr>"
                )

            # ── Customer / test instruction action rows ───────────────
            for action in (rec.get("Actions") or []):
                if action.get("Type") != "SendInstruction":
                    continue
                actual = action.get("ActualParameters") or action.get("ExpectedParameters") or {}
                instruction = actual.get("Instruction") or {}
                instr_type = instruction.get("Type", "")
                instr_props = instruction.get("Properties") or {}
                actor = (actual.get("Actor") or "CUSTOMER").upper()
                action_status = action.get("Status", "")
                status_style = (
                    "color:#1E8449" if action_status == "PASSED" else "color:#C0392B"
                )
                if instr_type == "DtmfInput":
                    content = f"DTMF: {_esc(instr_props.get('Value', '?'))}"
                elif instr_type == "SendMessage":
                    content = f"Message: {_esc(instr_props.get('Content', ''))}"
                else:
                    content = f"{_esc(instr_type)}: {_esc(str(instr_props))}"
                status_badge = (
                    f" <small style='{status_style}'>({_esc(action_status)})</small>"
                )
                rows.append(
                    f"<tr class='msg-customer'>"
                    f"<td>{step_name}</td>"
                    f"<td><strong>{_esc(actor)}</strong></td>"
                    f"<td>{content}{status_badge}</td>"
                    f"<td style='white-space:nowrap'>{event_ts}</td>"
                    f"</tr>"
                )

        elif rec_type == "COMPLETION":
            completion = rec.get("CompletionReason") or {}
            exec_summary = rec.get("ExecutionSummary") or {}
            obs_msg = ""
            if exec_summary.get("TotalObservations") is not None:
                obs_msg = (
                    f" | {exec_summary.get('PassedObservations', 0)}/"
                    f"{exec_summary.get('TotalObservations', 0)} observations passed"
                )
            rows.append(
                f"<tr class='msg-system'>"
                f"<td>{step_name}</td>"
                f"<td><strong>SYSTEM</strong></td>"
                f"<td>{_esc(completion.get('Type', 'COMPLETION'))}: "
                f"{_esc(completion.get('Message', ''))}{_esc(obs_msg)}</td>"
                f"<td style='white-space:nowrap'>{ts}</td>"
                f"</tr>"
            )

    if not rows:
        return "<p style='color:#888;padding:8px'>No transcript data available.</p>"

    return (
        "<div style='overflow-x:auto'>"
        "<table class='transcript-table'>"
        "<thead><tr>"
        "<th>Step</th><th>Participant</th><th>Message / Content</th><th>Timestamp</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def _step_attributes_html(rec: dict) -> str:
    """
    Render a rich HTML block for the Attributes cell of a step row.

    Matches the detail shown in the AWS Connect console test run view:
      • INITIATION     : call entry-point type, From/To phone numbers
      • EXECUTION_START: ContactId
      • OBSERVATION    : Observe section (event type + full properties +
                         expected/actual usage) + Actions section (per-action
                         type, instruction/behavior/command detail + status)
      • COMPLETION     : result type, message, observation pass/fail summary
    """
    rec_type = rec.get("Type", "")
    _D = "style='font-size:13px;line-height:1.7'"
    _LABEL = "style='font-weight:600;color:#444;font-size:12px;text-transform:uppercase;letter-spacing:.04em'"
    _ITEM  = "style='margin-left:14px;color:#333'"
    _PASS  = "style='color:#1E8449'"
    _FAIL  = "style='color:#C0392B'"

    # ── INITIATION ────────────────────────────────────────────────────────
    if rec_type == "INITIATION":
        ep = rec.get("EntryPoint") or {}
        params = ep.get("Parameters") or {}
        ep_type = _esc(ep.get("Type", ""))
        src = _esc(params.get("SourcePhoneNumber", ""))
        dst = _esc(params.get("DestinationPhoneNumber", ""))
        rows = []
        if ep_type:
            rows.append(f"<div {_D}><b>Type:</b> {ep_type}</div>")
        if src or dst:
            rows.append(f"<div {_D}><b>From:</b> {src} &rarr; <b>To:</b> {dst}</div>")
        return "".join(rows)

    # ── EXECUTION_START ───────────────────────────────────────────────────
    if rec_type == "EXECUTION_START":
        contact_id = rec.get("ContactId", "")
        if contact_id:
            return f"<div {_D}><b>ContactId:</b> \u2026{_esc(contact_id[-12:])}</div>"
        return ""

    # ── OBSERVATION ───────────────────────────────────────────────────────
    if rec_type == "OBSERVATION":
        event = rec.get("Event") or {}
        event_type = event.get("Type", "")
        event_props = event.get("Properties") or {}
        exp_usage = rec.get("ExpectedUsage") or {}
        act_usage = rec.get("ActualUsage") or {}

        # Build the single observe detail line
        detail_parts: list[str] = [_esc(event_type)] if event_type else []

        if event_type == "MessageReceived":
            if event_props.get("Text"):
                detail_parts.append(f"Text={_esc(event_props['Text'])}")
            if event_props.get("LanguageCode"):
                detail_parts.append(f"LanguageCode={_esc(event_props['LanguageCode'])}")
        elif event_type == "FlowActionStarted":
            if event_props.get("ActionType"):
                detail_parts.append(f"ActionType={_esc(event_props['ActionType'])}")
            ap = event_props.get("ActionParameters") or {}
            if ap.get("QueueId"):
                detail_parts.append(f"QueueId={_esc(ap['QueueId'].split('/')[-1])}")
        # Generic: any other event type with Properties
        else:
            for k, v in event_props.items():
                if v and not isinstance(v, dict):
                    detail_parts.append(f"{_esc(k)}={_esc(str(v))}")

        # Append expected usage
        exp_str_parts: list[str] = []
        if exp_usage.get("Type"):
            exp_str_parts.append(f"Type={_esc(exp_usage['Type'])}")
        if exp_usage.get("Times") is not None:
            exp_str_parts.append(f"Times={exp_usage['Times']}")
        if exp_str_parts:
            detail_parts.append(f"Expected: {', '.join(exp_str_parts)}")

        # Append received/actual usage
        act_str_parts: list[str] = []
        if act_usage.get("Times") is not None:
            act_str_parts.append(f"Times={act_usage['Times']}")
        if act_str_parts:
            detail_parts.append(f"Received: {', '.join(act_str_parts)}")

        observe_line = " \u2013 ".join(detail_parts)
        html_parts: list[str] = [
            f"<div style='margin-bottom:6px'>"
            f"<span {_LABEL}>Observe</span>"
            f"<div {_ITEM}>{observe_line}</div>"
            f"</div>"
        ]

        # Actions sub-section
        actions = rec.get("Actions") or []
        if actions:
            action_divs: list[str] = []
            for action in actions:
                action_type = action.get("Type", "?")
                action_status = action.get("Status", "")
                actual = action.get("ActualParameters") or action.get("ExpectedParameters") or {}
                style = _PASS if action_status == "PASSED" else (_FAIL if action_status == "FAILED" else "")
                st_attr = f" {style}" if style else ""

                adp: list[str] = [_esc(action_type)]

                if action_type == "OverrideSystemBehavior":
                    beh = (actual.get("Behavior") or {}).get("Properties") or {}
                    if beh.get("ActionType"):
                        adp.append(_esc(beh["ActionType"]))
                    strat = beh.get("Strategy") or {}
                    if strat.get("Type"):
                        adp.append(_esc(strat["Type"]))
                    resp = strat.get("Response") or {}
                    exec_res = resp.get("ExecutionResult") or {}
                    if exec_res.get("Value"):
                        adp.append(f"Type=ExecutionResult, Value={_esc(exec_res['Value'])}")

                elif action_type == "SendInstruction":
                    instr = actual.get("Instruction") or {}
                    if instr.get("Type"):
                        adp.append(_esc(instr["Type"]))
                    for k, v in (instr.get("Properties") or {}).items():
                        if v is not None:
                            adp.append(f"{_esc(k)}={_esc(str(v))}")

                elif action_type == "TestControl":
                    cmd = actual.get("Command") or {}
                    if cmd.get("Type"):
                        adp.append(_esc(cmd["Type"]))

                else:
                    # Generic: flatten top-level actual params
                    for k, v in actual.items():
                        if v is not None and not isinstance(v, (dict, list)):
                            adp.append(f"{_esc(k)}={_esc(str(v))}")

                action_line = " \u2013 ".join(adp)
                action_divs.append(f"<div {_ITEM}{st_attr}>{action_line}</div>")

            html_parts.append(
                f"<div>"
                f"<span {_LABEL}>Actions</span>"
                f"{''.join(action_divs)}"
                f"</div>"
            )

        return f"<div style='min-width:340px;font-size:13px;line-height:1.7'>{''.join(html_parts)}</div>"

    # ── COMPLETION ────────────────────────────────────────────────────────
    if rec_type == "COMPLETION":
        completion = rec.get("CompletionReason") or {}
        exec_summary = rec.get("ExecutionSummary") or {}
        rows: list[str] = []

        comp_type = completion.get("Type", "")
        if comp_type:
            colour = "#1E8449" if comp_type == "SUCCESS" else "#C0392B"
            rows.append(
                f"<div {_D}><b>Result:</b> "
                f"<span style='color:{colour};font-weight:600'>{_esc(comp_type)}</span></div>"
            )

        comp_msg = completion.get("Message", "")
        if comp_msg:
            rows.append(f"<div {_D}><b>Message:</b> {_esc(comp_msg)}</div>")

        failure_reasons = completion.get("FailureReasons") or []
        if failure_reasons:
            rows.append(
                f"<div {_D} style='color:#C0392B'>"
                f"<b>Failures:</b> {_esc('; '.join(str(fr) for fr in failure_reasons))}</div>"
            )

        if exec_summary.get("TotalObservations") is not None:
            total   = exec_summary.get("TotalObservations", 0)
            passed  = exec_summary.get("PassedObservations", 0)
            failed  = exec_summary.get("FailedObservations", 0)
            skipped = exec_summary.get("SkippedObservations", 0)
            obs_str = f"{passed}/{total} passed"
            if failed:
                obs_str += f", {failed} failed"
            if skipped:
                obs_str += f", {skipped} skipped"
            rows.append(f"<div {_D}><b>Obs:</b> {_esc(obs_str)}</div>")

        return "".join(rows)

    return ""


def _build_steps_table(steps: list[dict]) -> str:
    """
    Return an HTML table showing step-level execution details.

    Reads the Connect Testing record structure produced by
    _normalise_steps():  observation_id / timestamp / record{Type, Identifier,
    Event, Actions, EntryPoint, CompletionReason, ExecutionSummary, …}
    """
    if not steps:
        return "<p style='color:#888;padding:8px'>No step detail available.</p>"

    rows: list[str] = []
    for step in steps:
        rec = step.get("record") or {}
        rec_type = rec.get("Type", "")

        # ── Step ID (last 12 chars of observation UUID) ────────────────
        raw_obs_id = step.get("observation_id") or ""
        step_id_display = raw_obs_id[-12:] if raw_obs_id else "–"

        # ── Step Name ─────────────────────────────────────────────────
        step_name = rec.get("Identifier") or rec_type or "–"

        # ── Step Type (record Type + event sub-type for OBSERVATION) ──
        event = rec.get("Event") or {}
        if rec_type == "OBSERVATION" and event.get("Type"):
            step_type_display = f"{rec_type} / {event['Type']}"
        else:
            step_type_display = rec_type or "–"

        # ── Duration ──────────────────────────────────────────────────
        duration_ms = None
        if rec_type == "COMPLETION":
            exec_summary = rec.get("ExecutionSummary") or {}
            duration_ms = exec_summary.get("ExecutionDurationMs")

        # ── Failure reason (shown under status badge) ─────────────────
        failure_reason = ""
        if rec_type == "COMPLETION":
            completion = rec.get("CompletionReason") or {}
            failure_reasons = completion.get("FailureReasons") or []
            if failure_reasons:
                failure_reason = "; ".join(str(fr) for fr in failure_reasons)
        else:
            failed_actions = [
                a for a in (rec.get("Actions") or [])
                if a.get("Status") == "FAILED"
            ]
            if failed_actions:
                failure_reason = f"{len(failed_actions)} action(s) failed"

        failure_html = (
            f"<br/><small style='color:#c0392b'>{_esc(failure_reason)}</small>"
            if failure_reason else ""
        )

        rows.append(
            f"<tr>"
            f"<td style='white-space:nowrap'><small><code>{_esc(step_id_display)}</code></small></td>"
            f"<td style='white-space:nowrap'>{_esc(step_name)}</td>"
            f"<td style='white-space:nowrap'>{_esc(step_type_display)}</td>"
            f"<td style='white-space:nowrap'>{_badge(step.get('status'))}{failure_html}</td>"
            f"<td style='white-space:nowrap'>{_fmt_ts(step.get('timestamp'))}</td>"
            f"<td style='white-space:nowrap'>{_duration(duration_ms)}</td>"
            f"<td style='word-break:break-word'>{_step_attributes_html(rec)}</td>"
            f"</tr>"
        )

    return (
        "<div style='overflow-x:auto'>"
        "<table style='table-layout:auto'>"
        "<thead><tr>"
        "<th>Step ID</th><th>Name</th><th>Type</th><th>Status</th>"
        "<th>Started</th><th>Duration</th><th style='min-width:340px'>Attributes</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def _build_test_case_section(result: dict, include_transcripts: bool) -> str:
    """
    Return the full HTML block for a single test-case result including:
    • Header with status badge
    • Summary row
    • Step-by-step details (collapsible)
    • Transcript details (collapsible, if enabled)
    """
    tc_id    = _esc(result.get("test_case_id"))
    tc_name  = _esc(result.get("test_case_name"))
    status   = result.get("status", "UNKNOWN")
    steps    = result.get("steps", [])
    summary  = result.get("summary", {})
    error    = _esc(result.get("error") or "")
    tags     = ", ".join(_esc(t) for t in result.get("tags", []))

    # Expected vs actual
    expected = result.get("expected", {})
    exp_queue   = _esc(expected.get("queue"))
    exp_outcome = _esc(expected.get("outcome"))
    act_outcome = _esc(summary.get("status"))

    # Failure reason
    fail_reason = _esc(summary.get("failure_reason") or result.get("error") or "")
    fail_html   = (
        f"<tr><td colspan='2'><strong style='color:#c0392b'>Failure reason:</strong> {fail_reason}</td></tr>"
        if fail_reason else ""
    )

    steps_html      = _build_steps_table(steps)
    transcript_html = _build_transcript_table(steps) if include_transcripts else ""
    transcript_block = (
        f"<details><summary>Transcripts ({_count_transcripts(steps)} entries)</summary>"
        f"<div class='inner'>{transcript_html}</div></details>"
        if include_transcripts else ""
    )

    return f"""
    <div class='card' id='tc-{tc_id}' style='margin-bottom:12px'>
      <div class='card-header' style='display:flex;justify-content:space-between;align-items:center'>
        <span>{tc_name}</span>
        {_badge(status)}
      </div>
      <div style='padding:16px'>

        <table style='width:auto;margin-bottom:12px'>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Test Case ID</td><td><code>{tc_id}</code></td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Execution ID</td><td><code>{_esc(result.get('execution_id'))}</code></td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Suite</td><td>{_esc(result.get('suite_name'))}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Started</td><td>{_fmt_ts(result.get('started_at'))}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Finished</td><td>{_fmt_ts(result.get('finished_at'))}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Expected queue</td><td>{exp_queue or '–'}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Expected outcome</td><td>{exp_outcome or '–'}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Actual status</td><td>{act_outcome or '–'}</td></tr>
          <tr><td style='color:#666;padding:4px 12px 4px 0'>Tags</td><td>{tags or '–'}</td></tr>
          {fail_html}
        </table>

        <details>
          <summary>Step-by-step Details ({len(steps)} steps)</summary>
          <div class='inner'>{steps_html}</div>
        </details>

        {transcript_block}

      </div>
    </div>
    """


def _build_suite_summary_rows(results: list[dict]) -> str:
    """Return HTML table rows summarising pass/fail counts per test suite."""
    suites: dict[str, dict] = {}
    for r in results:
        sid = r.get("suite_id") or "UNKNOWN"
        sname = r.get("suite_name") or "Unknown"
        if sid not in suites:
            suites[sid] = {"name": sname, "total": 0, "pass": 0, "fail": 0}
        suites[sid]["total"] += 1
        if r.get("status") == "PASS":
            suites[sid]["pass"] += 1
        elif r.get("status") in {"FAIL", "ERROR", "TIMEOUT"}:
            suites[sid]["fail"] += 1

    rows = []
    for sid, d in suites.items():
        rate = f"{(d['pass'] / d['total'] * 100):.0f}%" if d["total"] else "–"
        pass_colour = "#1E8449" if d["fail"] == 0 else "#C0392B"
        rows.append(
            f"<tr>"
            f"<td>{_esc(sid)}</td>"
            f"<td>{_esc(d['name'])}</td>"
            f"<td>{d['total']}</td>"
            f"<td style='color:{pass_colour}'><strong>{d['pass']}</strong></td>"
            f"<td style='color:#C0392B'><strong>{d['fail']}</strong></td>"
            f"<td>{rate}</td>"
            f"</tr>"
        )
    return "".join(rows)


def write_html_report(run_payload: dict, output_path: Path, report_cfg: dict) -> None:
    """
    Generate and write a fully self-contained HTML test report.

    Parameters
    ----------
    run_payload : dict  – structured run result from run_connect_tests.py
    output_path : Path  – file path to write the HTML to
    report_cfg  : dict  – ``cfg["reporting"]`` sub-dict (controls theme + flags)
    """
    theme = report_cfg.get("html_theme", {})
    primary = theme.get("primary_color", "#232F3E")
    accent  = theme.get("accent_color",  "#FF9900")
    pass_c  = theme.get("pass_color",    "#1E8449")
    fail_c  = theme.get("fail_color",    "#C0392B")
    warn_c  = theme.get("warn_color",    "#E67E22")

    css = _CSS_TEMPLATE.format(
        primary=primary, accent=accent,
        pass_c=pass_c, fail_c=fail_c, warn_c=warn_c,
    )

    stats       = run_payload.get("statistics", {})
    total       = stats.get("total", 0)
    passed      = stats.get("passed", 0)
    failed      = stats.get("failed", 0)
    skipped     = stats.get("skipped", 0)
    timeout     = stats.get("timeout", 0)
    pass_rate   = stats.get("pass_rate", "–")
    pass_pct    = (passed / total * 100) if total else 0

    run_id      = _esc(run_payload.get("run_id"))
    instance_id = _esc(run_payload.get("instance_id"))
    region      = _esc(run_payload.get("region"))
    started_at  = _fmt_ts(run_payload.get("started_at"))
    finished_at = _fmt_ts(run_payload.get("finished_at"))

    results         = run_payload.get("results", [])
    include_xscript = report_cfg.get("include_transcripts", True)
    include_steps   = report_cfg.get("include_step_details", True)

    # ── Build per-test-case HTML ─────────────────────────────────────────
    test_sections = ""
    for r in results:
        if include_steps:
            test_sections += _build_test_case_section(r, include_xscript)
        else:
            # Minimal row only
            tc_anchor = _esc(r.get("test_case_id"))
            test_sections += (
                f"<tr>"
                f"<td><a href='#tc-{tc_anchor}'>"
                f"{_esc(r.get('test_case_name'))}</a></td>"
                f"<td>{_esc(r.get('suite_name'))}</td>"
                f"<td>{_badge(r.get('status'))}</td>"
                f"<td>{_fmt_ts(r.get('started_at'))}</td>"
                f"<td>{_fmt_ts(r.get('finished_at'))}</td>"
                f"</tr>"
            )

    suite_rows = _build_suite_summary_rows(results)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Amazon Connect Test Report – {started_at}</title>
  <style>{css}</style>
</head>
<body>
<div class="page-wrap">

  <!-- ═══ Page header ═══════════════════════════════════════════════════ -->
  <div class="page-header">
    <div class="logo">&#9881;</div>
    <div>
      <h1>Amazon Connect Dashboard Test Report</h1>
      <div class="sub">
        Run ID: {run_id} &nbsp;|&nbsp; Instance: {instance_id}
        &nbsp;|&nbsp; Region: {region}
        &nbsp;|&nbsp; {started_at} → {finished_at}
      </div>
    </div>
  </div>

  <!-- ═══ Stat bar ══════════════════════════════════════════════════════ -->
  <div class="stat-grid">
    <div class="stat-item stat-total"><div class="value">{total}</div><div class="label">Total</div></div>
    <div class="stat-item stat-pass"><div class="value">{passed}</div><div class="label">Passed</div></div>
    <div class="stat-item stat-fail"><div class="value">{failed}</div><div class="label">Failed</div></div>
    <div class="stat-item stat-warn"><div class="value">{timeout}</div><div class="label">Timeout</div></div>
    <div class="stat-item"><div class="value">{skipped}</div><div class="label">Skipped</div></div>
    <div class="stat-item stat-pass"><div class="value">{pass_rate}</div><div class="label">Pass Rate</div></div>
  </div>

  <!-- ═══ Pass-rate progress bar ════════════════════════════════════════ -->
  <div class="progress-wrap">
    <div class="progress-bar">
      <div class="progress-fill" style="width:{pass_pct:.1f}%"></div>
    </div>
  </div>

  <!-- ═══ Suite summary ═════════════════════════════════════════════════ -->
  <div class="card">
    <div class="card-header">Suite Summary</div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>Suite ID</th><th>Suite Name</th><th>Total</th>
          <th>Passed</th><th>Failed</th><th>Pass Rate</th>
        </tr></thead>
        <tbody>{suite_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ═══ Test-case results ══════════════════════════════════════════════ -->
  <h2 style="margin-bottom:12px;color:{primary}">Test Case Results</h2>
  {test_sections}

  <!-- ═══ Footer ════════════════════════════════════════════════════════ -->
  <div class="page-footer">
    Generated by Amazon Connect Dashboard Test Runner •
    {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
  </div>

</div><!-- .page-wrap -->
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    log.debug("HTML report written to %s (%d bytes)", output_path, len(html))


# ===========================================================================
# XML (JUnit-compatible) Report
# ===========================================================================

def write_xml_report(run_payload: dict, output_path: Path) -> None:
    """
    Generate and write a JUnit-compatible XML test report.

    The output conforms to the JUnit XML schema widely accepted by GitHub
    Actions test reporters, Jenkins, and other CI tools. Each Amazon Connect
    test suite produces a ``<testsuite>`` element; each test case maps to a
    ``<testcase>`` element.

    Additionally, step-level details and transcripts are embedded as
    ``<system-out>`` CDATA within each ``<testcase>``.

    Parameters
    ----------
    run_payload : dict  – structured run result from run_connect_tests.py
    output_path : Path  – file path to write the XML to
    """
    stats   = run_payload.get("statistics", {})
    results = run_payload.get("results", [])

    # ── Root testsuites element ──────────────────────────────────────────
    root = ET.Element("testsuites")
    root.set("name",      "Amazon Connect Dashboard Tests")
    root.set("tests",     str(stats.get("total", 0)))
    root.set("failures",  str(stats.get("failed", 0)))
    root.set("errors",    "0")
    root.set("skipped",   str(stats.get("skipped", 0)))
    root.set("time",      _calc_duration_seconds(run_payload))
    root.set("timestamp", run_payload.get("started_at", ""))
    root.set("id",        run_payload.get("run_id", ""))

    # ── Group results by suite ────────────────────────────────────────────
    suites: dict[str, list[dict]] = {}
    for r in results:
        sid = r.get("suite_id") or "DEFAULT"
        suites.setdefault(sid, []).append(r)

    for suite_id, suite_results in suites.items():
        suite_name  = (suite_results[0].get("suite_name") or suite_id) if suite_results else suite_id
        s_total     = len(suite_results)
        s_failures  = sum(1 for r in suite_results if r.get("status") in {"FAIL", "ERROR", "TIMEOUT"})
        s_skipped   = sum(1 for r in suite_results if r.get("status") == "SKIPPED")

        ts = ET.SubElement(root, "testsuite")
        ts.set("name",      suite_name)
        ts.set("id",        suite_id)
        ts.set("tests",     str(s_total))
        ts.set("failures",  str(s_failures))
        ts.set("errors",    "0")
        ts.set("skipped",   str(s_skipped))
        ts.set("timestamp", suite_results[0].get("started_at", "") if suite_results else "")

        # ── Per-test-case elements ────────────────────────────────────────
        for r in suite_results:
            tc = ET.SubElement(ts, "testcase")
            tc.set("name",      r.get("test_case_name", ""))
            tc.set("classname", suite_name)
            tc.set("time",      _result_duration_seconds(r))
            tc.set("id",        r.get("test_case_id", ""))
            tc.set("status",    r.get("status", "UNKNOWN"))

            status = r.get("status", "UNKNOWN").upper()

            if status == "SKIPPED":
                ET.SubElement(tc, "skipped").text = "Test was skipped / dry run"

            elif status in {"FAIL", "ERROR", "TIMEOUT"}:
                failure = ET.SubElement(tc, "failure")
                failure.set("type",    status)
                failure.set("message", r.get("summary", {}).get("failure_reason") or r.get("error") or status)
                failure.text = _build_failure_text(r)

            # ── system-out: step-by-step details + transcripts ────────────
            sys_out = ET.SubElement(tc, "system-out")
            sys_out.text = _build_system_out(r)

        # ── properties: config snapshot ───────────────────────────────────
        props = ET.SubElement(ts, "properties")
        snap  = run_payload.get("config_snapshot", {})
        for k, v in snap.items():
            prop = ET.SubElement(props, "property")
            prop.set("name",  str(k))
            prop.set("value", str(v))

    # ── Serialise ─────────────────────────────────────────────────────────
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)
    log.debug("XML report written to %s", output_path)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _calc_duration_seconds(run_payload: dict) -> str:
    """Compute total run duration in fractional seconds as a string."""
    try:
        t_start = datetime.fromisoformat(run_payload["started_at"])
        t_end   = datetime.fromisoformat(run_payload["finished_at"])
        return f"{(t_end - t_start).total_seconds():.3f}"
    except (KeyError, ValueError, TypeError):
        return "0"


def _result_duration_seconds(result: dict) -> str:
    """Compute a single test-case duration in fractional seconds."""
    try:
        t_start = datetime.fromisoformat(result["started_at"])
        t_end   = datetime.fromisoformat(result["finished_at"])
        return f"{(t_end - t_start).total_seconds():.3f}"
    except (KeyError, ValueError, TypeError):
        return "0"


def _build_failure_text(result: dict) -> str:
    """Build the text content for a <failure> element."""
    lines = []
    summary = result.get("summary", {})
    if summary.get("failure_reason"):
        lines.append(f"Failure reason : {summary['failure_reason']}")
    if result.get("error"):
        lines.append(f"Error          : {result['error']}")
    lines.append(f"Expected queue : {result.get('expected', {}).get('queue') or '–'}")
    lines.append(f"Expected outcome: {result.get('expected', {}).get('outcome') or '–'}")
    lines.append(f"Actual status  : {summary.get('status') or '–'}")

    # Step failures
    failed_steps = [s for s in result.get("steps", []) if s.get("status") not in {"PASS", "PASSED"}]
    if failed_steps:
        lines.append("\nFailed steps:")
        for s in failed_steps:
            lines.append(
                f"  [{s.get('step_id')}] {s.get('step_name')} "
                f"({s.get('step_type')}) – {s.get('failure_reason') or s.get('status')}"
            )
    return "\n".join(lines)


def _build_system_out(result: dict) -> str:
    """
    Build the plain-text content for``<system-out>`` inside a ``<testcase>``.
    Includes step table + transcript dump.
    """
    lines = []

    # ── Step details ──────────────────────────────────────────────────────
    steps = result.get("steps", [])
    if steps:
        lines.append("=" * 60)
        lines.append("STEP-BY-STEP EXECUTION DETAILS")
        lines.append("=" * 60)
        lines.append(
            f"{'Step ID':<20} {'Name':<30} {'Type':<25} {'Status':<12} {'Duration':>10}"
        )
        lines.append("-" * 100)
        for s in steps:
            lines.append(
                f"{str(s.get('step_id','')):<20} "
                f"{str(s.get('step_name','')):<30} "
                f"{str(s.get('step_type','')):<25} "
                f"{str(s.get('status','')):<12} "
                f"{_duration(s.get('duration_ms')):>10}"
            )
            if s.get("failure_reason"):
                lines.append(f"  ↳ FAILURE: {s['failure_reason']}")
        lines.append("")

    # ── Transcripts ───────────────────────────────────────────────────────
    transcript_entries = []
    for step in steps:
        for t in step.get("transcript", []):
            transcript_entries.append((
                step.get("step_name") or step.get("step_id") or "Step",
                t.get("participant", ""),
                t.get("content", ""),
                t.get("timestamp", ""),
            ))

    if transcript_entries:
        lines.append("=" * 60)
        lines.append("TRANSCRIPTS")
        lines.append("=" * 60)
        for step_name, participant, content, ts in transcript_entries:
            lines.append(f"[{ts}] [{step_name}] {participant}: {content}")
        lines.append("")

    return "\n".join(lines)
