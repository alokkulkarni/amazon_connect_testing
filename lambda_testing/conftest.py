"""
conftest.py – pytest hooks for lambda_testing.

Collects per-test pass/fail/skip results and writes:
  - reports/lambda_test_report.json   (machine-readable)
  - console table summary             (printed after the session)

The HTML report is produced by pytest-html; pass
  --html=reports/lambda_test_report.html
to run_lambda_tests.sh / pytest directly.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(_HERE, "reports")


def pytest_configure(config):
    os.makedirs(REPORT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------

class _ResultCollector:
    def __init__(self):
        self.results: list[dict] = []

    def record(self, name: str, outcome: str, duration: float, error: str | None = None):
        self.results.append(
            {
                "name": name,
                "outcome": outcome,       # PASSED / FAILED / SKIPPED / ERROR
                "duration_s": round(duration, 3),
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


_collector = _ResultCollector()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    # Only capture the "call" phase (or "setup" if setup itself failed)
    if report.when == "call" or (report.when == "setup" and report.failed):
        error_text = None
        if report.failed:
            status = "FAILED"
            error_text = str(report.longrepr) if report.longrepr else None
        elif report.skipped:
            status = "SKIPPED"
            error_text = str(report.longrepr) if report.longrepr else None
        else:
            status = "PASSED"

        _collector.record(
            name=item.name,
            outcome=status,
            duration=report.duration,
            error=error_text,
        )


# ---------------------------------------------------------------------------
# Session-finish: write JSON + print table
# ---------------------------------------------------------------------------

def pytest_sessionfinish(session, exitstatus):
    results = _collector.results
    if not results:
        return

    passed  = sum(1 for r in results if r["outcome"] == "PASSED")
    failed  = sum(1 for r in results if r["outcome"] == "FAILED")
    skipped = sum(1 for r in results if r["outcome"] == "SKIPPED")
    errored = sum(1 for r in results if r["outcome"] == "ERROR")
    total   = len(results)

    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errored": errored,
        },
        "test_cases": results,
    }

    json_path = os.path.join(REPORT_DIR, "lambda_test_report.json")
    with open(json_path, "w") as fh:
        json.dump(report_data, fh, indent=2)

    # -----------------------------------------------------------------------
    # Console table
    # -----------------------------------------------------------------------
    width = 74
    print("\n" + "=" * width)
    print("  LAMBDA AUTOMATION TEST REPORT")
    print("=" * width)
    print(f"  {'Test Case':<52} {'Result':<10} {'Duration'}")
    print("-" * width)
    for r in results:
        icon = {"PASSED": "PASS", "FAILED": "FAIL", "SKIPPED": "SKIP", "ERROR": "ERRO"}.get(
            r["outcome"], "????"
        )
        name_display = r["name"]
        if len(name_display) > 50:
            name_display = name_display[:47] + "..."
        print(f"  [{icon}]  {name_display:<50} {r['duration_s']:>6.2f}s")
    print("-" * width)
    print(
        f"  Total: {total}  |  "
        f"Passed: {passed}  |  "
        f"Failed: {failed}  |  "
        f"Skipped: {skipped}  |  "
        f"Errored: {errored}"
    )
    print("=" * width)
    print(f"  JSON report  : {json_path}")
    html_path = os.path.join(REPORT_DIR, "lambda_test_report.html")
    if os.path.exists(html_path):
        print(f"  HTML report  : {html_path}")
    print("=" * width + "\n")
