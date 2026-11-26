"""Tests for the Notion integration."""

import os
import unittest.mock as mock


def test_is_configured_false_by_default():
    from sentinel.publish.notion import is_configured

    env = {k: v for k, v in os.environ.items() if k not in ("NOTION_API_KEY", "NOTION_DATABASE_ID")}
    with mock.patch.dict(os.environ, env, clear=True):
        assert not is_configured()


def test_build_page_properties_basic():
    from sentinel.publish.notion import build_page_properties

    report = {
        "decision": "BLOCK_HIGH",
        "risk": 0.89,
        "calibrated": True,
        "run_id": "run-test-001",
        "created_at": "2026-01-01T00:00:00Z",
        "pr": {"number": 42, "title": "Fix auth", "author": "dev"},
        "repository": "org/repo",
        "elapsed_s": 4.1,
        "verification": None,
    }
    props = build_page_properties(report)
    assert "Name" in props
    assert props["Decision"]["select"]["name"] == "BLOCK_HIGH"
    assert props["Base Decision"]["select"]["name"] == "BLOCK"
    assert props["Risk Level"]["select"]["name"] == "HIGH"
    assert props["Risk Score"]["number"] == 89.0


def test_build_page_content_returns_list():
    from sentinel.publish.notion import build_page_content

    report = {
        "decision": "BLOCK_HIGH",
        "risk": 0.89,
        "calibrated": True,
        "run_id": "run-test-001",
        "pr": {"number": 42, "title": "Fix auth", "author": "dev"},
        "repository": "org/repo",
        "decision_reason": "risk high",
        "contributions": [],
        "verification": None,
        "claims": [],
        "verdicts": [],
        "elapsed_s": 4.1,
    }
    blocks = build_page_content(report, dashboard_url="http://localhost:5173")
    assert isinstance(blocks, list)
    assert len(blocks) > 0


def test_rich_text_helper():
    from sentinel.publish.notion import _rich_text

    result = _rich_text("hello")
    assert isinstance(result, list)
    assert result[0]["text"]["content"] == "hello"
