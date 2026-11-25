import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.config import Config
from sentinel.risk.severity import with_verdict
from sentinel.publish.slack import SlackPublisher, dashboard_report_url, evidence_card, render_message
from sentinel.publish.slack_relay import relay_payload
from sentinel.service.slack import SlackIntegration, interaction_router, notify_completed
from sentinel.service.runs import RunManager

RUN = "run-20261124000000-abcdef"
OLDER = "run-20261123000000-abcdef"
HEAD = "a" * 40
REPO = "acme/project"
CHANNEL = "C012345"
USER = "U012345"
TEAM = "T012345"
SECRET = "fixture-signing-secret"


class AppAuth:
    configured = True

    def token(self, repo, permissions):
        assert repo == REPO and permissions == {"pull_requests": "write"}
        return "fixture-installation-token"


@pytest.fixture
def report():
    return {
        "schema": "sentinelpr.run/1", "repository": REPO, "run_id": RUN, "created_at": "2026-11-24T00:00:00+00:00",
        "decision": "BLOCK", "risk": 0.8895, "decision_reason": "Risk crosses the block threshold.",
        "thresholds": {"canary": 0.4982, "block": 0.8672},
        "pr": {"head_sha": HEAD, "number": 17, "title": "Update billing", "author": "dev"},
        "changes": [{"path": "billing.py"}],
        "claims": [{"target": "billing", "assertion": "Billing is impacted", "type": "module_impact", "status": "VERIFIED"}],
        "contributions": [{"label": "Missing coverage", "contribution": 0.3}],
        "verification": {"tests": {"selected": 10, "passed": 7, "failed": []},
                         "changed_lines": {"coverage": 0.5}, "mutation": {"score": 0.25}},
    }


@pytest.fixture
def system(monkeypatch, tmp_path, report):
    for name, value in {
        "SLACK_ENABLED": "true", "SLACK_TEAM_ID": TEAM, "SLACK_SIGNING_SECRET": SECRET, "SLACK_CHANNEL_ID": CHANNEL,
        "SLACK_ALLOWED_REVIEWERS": USER, "SLACK_REPOSITORIES": REPO, "SENTINEL_PUBLIC_BASE_URL": "https://sentinel.example.com",
        "SLACK_STATE_DIR": str(tmp_path / "slack"), "SLACK_RELAY_TOKEN": "fixture-relay",
    }.items():
        monkeypatch.setenv(name, value)
    state = {"slack": [], "github": [], "head": HEAD, "github_status": 200, "slack_error": None}

    def slack_http(request):
        method = request.url.path.rsplit("/", 1)[-1]
        data = json.loads(request.content)
        state["slack"].append((method, data))
        if method == "chat.postMessage" and state["slack_error"]:
            return httpx.Response(200, json={"ok": False, "error": state["slack_error"]})
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "team_id": TEAM})
        return httpx.Response(200, json={"ok": True, "ts": f"123.000{len(state['slack'])}", "channel": CHANNEL})

    def github_http(request):
        assert request.headers["authorization"] == "Bearer fixture-installation-token"
        if request.method == "GET":
            return httpx.Response(200, json={"head": {"sha": state["head"]}, "state": "open"})
        state["github"].append(json.loads(request.content))
        return httpx.Response(state["github_status"], json={"id": 24})

    cfg = Config.load(tmp_path, environ={})
    slack = SlackPublisher("fixture-bot-token", httpx.Client(base_url="https://slack.com/api/", transport=httpx.MockTransport(slack_http)))
    github = httpx.Client(base_url="https://api.github.com/", transport=httpx.MockTransport(github_http))
    integration = SlackIntegration(cfg, AppAuth(), slack, github)
    app = FastAPI()
    app.include_router(interaction_router(integration, cfg))
    return cfg, integration, TestClient(app), state


def signed(payload, timestamp=None):
    data = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = hmac.new(SECRET.encode(), f"v0:{timestamp}:".encode() + data, hashlib.sha256).hexdigest()
    return data, {"x-slack-signature": f"v0={signature}", "x-slack-request-timestamp": timestamp,
                  "content-type": "application/x-www-form-urlencoded"}


def send(client, payload, timestamp=None):
    data, headers = signed(payload, timestamp)
    return client.post("/api/slack/interactions", content=data, headers=headers)


def button(integration, action="sentinel_approve", user=USER):
    ts = integration.store.get(RUN)["ts"]
    return {"type": "block_actions", "team": {"id": TEAM}, "user": {"id": user}, "trigger_id": "fixture-trigger",
            "container": {"channel_id": CHANNEL, "message_ts": ts}, "actions": [{"action_id": action, "value": RUN}]}


def submission(action="approved", comment=None, user=USER):
    return {"type": "view_submission", "team": {"id": TEAM}, "user": {"id": user, "name": "Reviewer"},
            "view": {"callback_id": "sentinel_review", "private_metadata": json.dumps([RUN, action, USER]),
                     "state": {"values": {"reason": {"comment": {"value": comment}}}}}}


def test_message_contains_real_evidence_and_unavailable_values(report):
    card = evidence_card(report | {"verification_checkout_sha": "b" * 40}, REPO,
                         dashboard_report_url("https://sentinel.example.com", RUN))
    message = render_message(card)
    encoded = json.dumps(message)
    assert message["attachments"][0]["color"] == "#DA3633"
    for value in ["BLOCK_LOW", "0.8895", "Risk crosses", "billing.py", "Billing is impacted", "50.0%", "25.0%", "Pending",
                  "Approve PR", "Reject / Request Changes", "View GitHub PR", "View Analysis Report"]:
        assert value in encoded
    assert card["executed"] == 7 and card["selected"] == 10 and card["failed"] == 0
    assert "Verification checkout" in encoded
    assert "Unavailable" in json.dumps(render_message(evidence_card(report | {"verification": None, "risk": None}, REPO)))
    assert dashboard_report_url("https://localhost:5173", RUN) is None


def test_disabled_and_repository_destinations_fail_closed(system, report):
    cfg, integration, _, state = system
    integration.enabled = False
    assert notify_completed(cfg, report, integration)["delivery"] == "disabled"
    integration.enabled = True
    assert integration.notify(report | {"repository": "elsewhere/private"})["delivery"] == "skipped"
    assert not state["slack"]


def test_delivery_errors_retry_and_duplicate_prevention(system, report):
    _, integration, _, state = system
    state["slack_error"] = "channel_not_found"
    assert integration.notify(report)["delivery_error"] == "channel_not_found"
    assert integration.status(RUN)["delivery"] == "failed"
    state["slack_error"] = None
    assert integration.notify(report)["delivery"] == "failed"
    assert integration.notify(report, resend=True)["delivery"] == "sent"
    assert integration.notify(report)["delivery"] == "sent"
    assert integration.notify(report, resend=True, allow_refresh=True)["delivery"] == "sent"
    assert [m for m, _ in state["slack"]] == ["chat.postMessage", "chat.postMessage", "chat.update"]


def test_callbacks_validate_signature_timestamp_replay_and_repository_permissions(system, report):
    _, integration, client, state = system
    integration.notify(report)
    p = button(integration)
    body, headers = signed(p)
    assert client.post("/api/slack/interactions", content=body, headers=headers | {"x-slack-signature": "v0=forged"}).status_code == 401
    assert send(client, p, int(time.time()) - 301).status_code == 401
    assert send(client, button(integration, user="U99999")).status_code == 403
    assert send(client, p | {"container": {"channel_id": "COTHER", "message_ts": "123.0001"}}).status_code == 400
    for _ in range(2):
        assert client.post("/api/slack/interactions", content=body, headers=headers).status_code == 200
    assert [m for m, _ in state["slack"]].count("views.open") == 1
    assert send(client, submission(user="U99999")).status_code == 403


@pytest.mark.parametrize(("action", "event", "comment", "http_status", "sync"),
                         [("approved", "APPROVE", None, 200, "synced"),
                          ("changes_requested", "REQUEST_CHANGES", "Please add tests", 403, "failed")])
def test_reviews_are_recorded_synced_once_and_original_message_is_updated(system, report, action, event, comment, http_status, sync):
    _, integration, client, state = system
    state["github_status"] = http_status
    integration.notify(report)
    if action == "changes_requested":
        assert send(client, submission(action)).json()["response_action"] == "errors"
    assert send(client, submission(action, comment)).status_code == 200
    assert send(client, submission(action, "different submission")).status_code == 200
    status = integration.status(RUN)
    assert status["review"]["status"] == action and status["review"]["sync"] == sync
    assert status["review"]["reviewer_name"] == "Reviewer"
    payload, = state["github"]
    assert payload["event"] == event and payload["commit_id"] == HEAD and USER in payload["body"]
    assert "BLOCK_LOW" in payload["body"]
    method, message = state["slack"][-1]
    assert method == "chat.update" and message["ts"] == "123.0001"
    assert "sentinel_approve" not in json.dumps(message)
    if comment:
        assert comment in json.dumps(message)


def test_stale_head_and_new_notification(system, report):
    _, integration, client, state = system
    older = report | {"run_id": OLDER, "created_at": "2026-11-23T00:00:00+00:00", "pr": report["pr"] | {"head_sha": "b" * 40}}
    integration.notify(older)
    integration.notify(report)
    assert integration.status(OLDER)["review"]["status"] == "outdated"
    state["head"] = "c" * 40
    assert send(client, submission()).status_code == 200
    assert integration.status(RUN)["review"]["status"] == "outdated"
    assert not state["github"]


def test_trusted_relay_validates_provenance_auth_and_deduplication(system, report):
    _, _, client, state = system
    event = {"repository": {"id": 4, "full_name": REPO}, "workflow_run": {"id": 123, "event": "pull_request",
             "pull_requests": [{"number": 17, "base": {"repo": {"id": 4}}, "head": {"sha": HEAD}}]}}
    with pytest.raises(ValueError):
        relay_payload(event, report | {"pr": {"number": 99}})
    payload = relay_payload(event, report)
    assert client.post("/api/slack/relay", json=payload).status_code == 401
    for _ in range(2):
        assert client.post("/api/slack/relay", json=payload, headers={"authorization": "Bearer fixture-relay"}).json()["delivery"] == "sent"
    assert [m for m, _ in state["slack"]] == ["chat.postMessage"]
    assert f"https://github.com/{REPO}/actions/runs/123#artifacts" in json.dumps(state["slack"][0][1])


def test_relay_secret_is_repository_scoped(system, report, monkeypatch):
    _, integration, client, state = system
    integration.routes[REPO] = {"relay_token_env": "SLACK_RELAY_TOKEN_PROJECT"}
    integration.routes["acme/other"] = {"relay_token_env": "SLACK_RELAY_TOKEN_OTHER"}
    monkeypatch.setenv("SLACK_RELAY_TOKEN_PROJECT", "fixture-project")
    monkeypatch.setenv("SLACK_RELAY_TOKEN_OTHER", "fixture-other")
    payload = {"report": report, "actions_run_id": 123}
    for token in ["fixture-relay", "fixture-other"]:
        assert client.post("/api/slack/relay", json=payload, headers={"authorization": f"Bearer {token}"}).status_code == 401
    assert not state["slack"]
    assert client.post("/api/slack/relay", json=payload, headers={"authorization": "Bearer fixture-project"}).json()["delivery"] == "sent"


def test_test_notification_contains_no_analysis_data(system):
    _, integration, _, state = system
    assert integration.check()["ok"]
    assert integration.check(test=True)["ok"]
    method, payload = state["slack"][-1]
    assert method == "chat.postMessage" and payload["channel"] == CHANNEL and REPO not in payload["text"]


@pytest.mark.parametrize("slack_error", [None, "channel_not_found"])
def test_service_delivers_the_single_completed_report(system, report, monkeypatch, tmp_path, slack_error):
    cfg, integration, _, state = system
    state["slack_error"] = slack_error
    repo = {"status": "ready", "full_name": REPO, "kind": "github"}
    spec = SimpleNamespace(cfg=cfg.with_overrides({"sandbox": {"mode": "docker"}}), base="b" * 40,
                           head=HEAD, info={"execution": "docker"}, pr=report["pr"])
    registry = SimpleNamespace(get=lambda _: repo, execution_mode=lambda _: "docker", prepare=lambda *a, **kw: spec)
    manager = RunManager(cfg, registry, tmp_path / "service")
    manager.slack = integration
    analyzed = []

    def analyse(config, *, run_id, **kwargs):
        analyzed.append(run_id)
        return with_verdict(report | {"run_id": run_id, "repository": config.get("publish.repository")})

    monkeypatch.setattr("sentinel.run.analyse", analyse)
    meta = manager.submit("acme-project", "17", llm=False, publish=False)
    manager.pool.shutdown(wait=True)
    run_id = meta["run_id"]
    assert analyzed == [run_id]
    assert manager.get_meta(run_id)["status"] == "completed"
    assert manager.get_report(run_id)["detailed_decision"] == "BLOCK_LOW"
    assert integration.status(run_id)["delivery"] == ("failed" if slack_error else "sent")
    assert [m for m, _ in state["slack"]] == ["chat.postMessage"]
