"""Tests for the Slack integration (mocked HTTP)."""

import hashlib
import hmac
import os
import unittest.mock as mock


def test_is_configured_false_by_default():
    from sentinel.publish.slack import is_configured

    env_no_slack = {k: v for k, v in os.environ.items() if k not in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL")}
    with mock.patch.dict(os.environ, env_no_slack, clear=True):
        assert not is_configured()


def test_build_blocks_returns_list():
    from sentinel.publish.slack import build_blocks

    report = {
        "decision": "BLOCK_HIGH",
        "risk": 0.89,
        "calibrated": True,
        "run_id": "run-test-001",
        "pr": {"number": 42, "title": "Fix auth", "author": "dev"},
        "repository": "org/repo",
        "decision_reason": "risk 0.89 >= block threshold 0.87",
        "contributions": [{"feature": "surviving_mutants", "contribution": 1.35}],
        "verification": {
            "tests": {"selected": 10, "passed": 10, "failed": [], "preexisting_failures": []},
            "changed_lines": {"coverage": 1.0},
            "mutation": {"score": 0.25, "mutants": [{"status": "survived"}]},
        },
        "selection": {"total_available": 107, "time_saved_ratio": 0.9},
        "elapsed_s": 4.1,
        "guard_events": [],
        "claims": [],
        "verdicts": [],
    }
    blocks = build_blocks(report, dashboard_url="http://localhost:5173")
    assert isinstance(blocks, list)
    assert len(blocks) > 0
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert action_blocks
    action_ids = {e["action_id"] for e in action_blocks[0]["elements"]}
    assert {"sentinel_reject", "sentinel_rerun", "sentinel_ask", "sentinel_view_report"} <= action_ids


def test_format_verdict_text():
    from sentinel.publish.slack import format_verdict_text

    report = {
        "decision": "BLOCK_HIGH",
        "risk": 0.89,
        "pr": {"number": 42},
        "repository": "org/repo",
    }
    text = format_verdict_text(report)
    assert "BLOCK_HIGH" in text
    assert "42" in text
    assert "org/repo" in text


def test_verify_slack_signature_no_secret():
    from sentinel.publish.slack import verify_slack_signature

    with mock.patch.dict(os.environ, {}, clear=True):
        assert verify_slack_signature(b"body", "12345", "v0=abc") is False


def test_verify_slack_signature_valid():
    from sentinel.publish.slack import verify_slack_signature

    secret = "test_signing_secret"
    body = b"payload=%7B%7D"
    ts = "1700000000"
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    with mock.patch.dict(os.environ, {"SLACK_SIGNING_SECRET": secret}):
        assert verify_slack_signature(body, ts, sig) is True
        assert verify_slack_signature(body, ts, "v0=wrong") is False


def test_handle_interactive_payload_actions():
    from sentinel.publish.slack import handle_interactive_payload

    for action_id, expected in [
        ("sentinel_approve", "approve"),
        ("sentinel_reject", "reject"),
        ("sentinel_rerun", "rerun"),
        ("sentinel_ask", "ask"),
    ]:
        payload = {
            "actions": [{"action_id": action_id, "value": "run-123"}],
            "user": {"name": "testuser"},
        }
        result = handle_interactive_payload(payload)
        assert result["action"] == expected
        assert result["run_id"] == "run-123"
        assert result["user"] == "testuser"
