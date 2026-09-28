"""pytest plugin loaded into the analysed project's test run (``-p sentinel.verify.pytest_plugin``).

It records every test's node id, outcome and duration and writes them as JSON to the path
in ``SENTINEL_TEST_REPORT``, so SentinelPR never has to scrape pytest's console output.
"""

from __future__ import annotations

import json
import os

_results: dict[str, dict] = {}
_collection_errors: list[str] = []
_collected: list[str] = []


def _entry(nodeid: str) -> dict:
    return _results.setdefault(nodeid, {"nodeid": nodeid, "outcome": "passed", "duration_s": 0.0, "message": ""})


def pytest_collection_modifyitems(items):
    _collected.extend(item.nodeid for item in items)


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append(f"{report.nodeid}: {str(report.longrepr)[-1500:]}")


def pytest_runtest_logreport(report):
    entry = _entry(report.nodeid)
    entry["duration_s"] += report.duration
    if report.failed:
        entry["outcome"] = "failed" if report.when == "call" else "error"
        entry["message"] = str(report.longreprtext or report.longrepr)[-2000:]
    elif report.skipped and report.when in ("setup", "call") and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get("SENTINEL_TEST_REPORT")
    if not target:
        return
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "exit_status": int(exitstatus),
                "collected": _collected,
                "collection_errors": _collection_errors,
                "results": list(_results.values()),
            },
            handle,
        )
