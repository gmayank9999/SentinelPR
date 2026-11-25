"""Slack Web API transport and Block Kit rendering for completed analysis reports."""

from __future__ import annotations

from urllib.parse import quote, urlsplit
import ipaddress
import re

import httpx

from sentinel.risk.severity import with_verdict
from sentinel.service.events import redact

STYLE = {"PASS": ("🟢", "#238636"), "CANARY": ("🟠", "#C66A00"), "BLOCK": ("🔴", "#DA3633")}
REVIEW_ACTIONS = {"sentinel_approve": "approved", "sentinel_request_changes": "changes_requested"}
REVIEW_LABEL = {"pending": "Pending", "processing": "Pending GitHub check", "approved": "Approved",
                "changes_requested": "Changes Requested", "outdated": "Outdated"}


class SlackError(RuntimeError):
    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(code)
        self.uncertain = uncertain


class SlackPublisher:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token = token
        self.client = client or httpx.Client(base_url="https://slack.com/api/", timeout=4, follow_redirects=False)

    def call(self, method: str, **payload) -> dict:
        try:
            response = self.client.post(method, headers={"Authorization": f"Bearer {self.token}"}, json=payload)
        except httpx.HTTPError as exc:
            raise SlackError("slack_transport", uncertain=True) from exc
        if not response.is_success:
            raise SlackError(f"slack_http_{response.status_code}", uncertain=response.status_code >= 500)
        try:
            data = response.json()
        except ValueError as exc:
            raise SlackError("slack_invalid_response", uncertain=True) from exc
        if not data.get("ok"):
            error = data.get("error", "")
            raise SlackError(error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error) else "slack_error")
        return data


def public_https(value: str | None) -> str | None:
    if not value:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme != "https" or "." not in host or url.username or url.password or url.query or url.fragment:
            return None
        if host.lower().endswith((".localhost", ".local", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return value.rstrip("/")
    except ValueError:
        return None


def evidence_card(report: dict, repo: str, report_url: str | None = None) -> dict:
    """Select a bounded, human-readable subset. Exclude source, diffs and test output."""
    r = with_verdict(report)
    pr = r.get("pr") or {}
    verification = r.get("verification") or {}
    tests = verification.get("tests") or {}
    passed = tests.get("passed")
    failed = len(tests["failed"]) if isinstance(tests.get("failed"), list) else None
    selected = tests.get("selected")
    if selected is None and isinstance((r.get("selection") or {}).get("tests"), list):
        selected = len(r["selection"]["tests"])
    claims = [c for c in r.get("claims", []) if c.get("status") == "VERIFIED"]
    override = r.get("policy_override") or {}
    return redact({
        "run_id": r["run_id"], "repository": repo, "number": pr["number"], "head": pr["head_sha"],
        "verification_checkout": r.get("verification_checkout_sha"),
        "title": (pr.get("title") or "")[:250], "author": (pr.get("author") or "")[:100],
        "created_at": r.get("created_at"), "decision": r["decision"], "detail": r["detailed_decision"],
        "risk": r["risk_score"], "reason": (r.get("decision_reason") or "")[:450],
        "override": (override.get("reason") or "")[:450], "action": r["recommended_action"],
        "risk_factors": [f"{c['label'][:100]} ({c['contribution']:+.2f})" for c in r.get("contributions", [])[:4]],
        "modules": [c["target"][:100] for c in claims if c.get("type") == "module_impact"][:6],
        "files": [c["path"][:120] for c in r.get("changes", [])[:8]],
        "selected": selected, "executed": passed + failed if passed is not None and failed is not None else None,
        "passed": passed, "failed": failed,
        "coverage": (verification.get("changed_lines") or {}).get("coverage"),
        "mutation": (verification.get("mutation") or {}).get("score"),
        "findings": [(c.get("assertion") or c["target"])[:160] for c in claims[:4]],
        "report_url": report_url,
    })


def _plain(value, limit: int = 2000) -> dict:
    return {"type": "plain_text", "text": str(value)[:limit] or "Unavailable", "emoji": True}


def _field(label: str, value) -> dict:
    return _plain(f"{label}\n{value if value is not None and value != '' else 'Unavailable'}")


def _section(label: str, value) -> dict:
    return {"type": "section", "text": _field(label, value)}


def _list(values, empty="None recorded") -> str:
    return "\n".join(f"• {value}" for value in values) or empty


def _number(value, format_spec: str):
    return format(value, format_spec) if value is not None else None


def render_message(card: dict, review: dict | None = None, *, interactive: bool = True) -> dict:
    review = review or {"status": "pending"}
    decision = card["decision"]
    icon, color = STYLE[decision]
    heading = card["detail"] or decision
    pr_url = f"https://github.com/{card['repository']}/pull/{card['number']}"
    blocks = [
        {"type": "header", "text": _plain(f"{icon} SentinelPR · {heading}", 150)},
        _section(f"{card['repository']} · PR #{card['number']}", card["title"]),
        {"type": "section", "fields": [
            _field("Primary verdict", decision), _field("Risk score", _number(card["risk"], ".4f")),
            _field("PR author", card["author"]), _field("Analyzed PR head", card["head"]),
            _field("Analysis timestamp", card["created_at"]), _field("Severity subtype", card["detail"]),
        ]},
        _section("Decision rationale", card["reason"]),
    ]
    checkout = card.get("verification_checkout")
    if checkout and checkout != card["head"]:
        blocks.append(_section("Verification checkout (GitHub Actions)", checkout))
    if card["override"]:
        blocks.append(_section("Policy override", card["override"]))
    blocks.extend([
        {"type": "section", "fields": [
            _field("Selected tests", card["selected"]), _field("Executed tests (passed or failed)", card["executed"]),
            _field("Passed", card["passed"]), _field("Failed (including errors)", card["failed"]),
            _field("Changed-line coverage", _number(card["coverage"], ".1%")), _field("Mutation score", _number(card["mutation"], ".1%")),
        ]},
        _section("Key risk factors", _list(card["risk_factors"])),
        {"type": "section", "fields": [
            _field("Verified impacted modules", _list(card["modules"])), _field("Changed files", _list(card["files"])),
        ]},
        _section("Important verified findings", _list(card["findings"])),
        _section("Recommended next action", card["action"]),
    ])
    status = review["status"]
    fields = [_field("Review status", REVIEW_LABEL[status])]
    if review.get("reviewer"):
        reviewer = f"{review['reviewer_name']} ({review['reviewer']})" if review.get("reviewer_name") else review["reviewer"]
        fields.extend([_field("Approved by" if status == "approved" else "Reviewed by", reviewer), _field("Review timestamp", review.get("reviewed_at"))])
    if review.get("comment"):
        fields.append(_field("Rejection reason" if review.get("decision") == "changes_requested" else "Approval comment", review["comment"]))
    if review.get("sync"):
        fields.append(_field("GitHub synchronization", f"{review['sync']} · {review['sync_detail']}" if review.get("sync_detail") else review["sync"]))
    blocks.append({"type": "section", "fields": fields})
    actions = []
    if status == "pending" and interactive:
        actions.extend([
            {"type": "button", "text": _plain("Approve PR"), "style": "primary", "action_id": "sentinel_approve", "value": card["run_id"]},
            {"type": "button", "text": _plain("Reject / Request Changes"), "style": "danger", "action_id": "sentinel_request_changes", "value": card["run_id"]},
        ])
    actions.append({"type": "button", "text": _plain("View GitHub PR"), "action_id": "sentinel_view_pr", "url": pr_url})
    if card["report_url"]:
        actions.append({"type": "button", "text": _plain("View Analysis Report"), "action_id": "sentinel_view_report", "url": card["report_url"]})
    else:
        blocks.append(_section("Analysis report link", None))
    blocks.append({"type": "actions", "elements": actions})
    fallback = f"SentinelPR {heading} · {card['repository']} PR #{card['number']} · {REVIEW_LABEL[status]}"
    return {"text": fallback, "attachments": [{"color": color, "fallback": fallback, "blocks": blocks}],
            "unfurl_links": False, "unfurl_media": False}


def dashboard_report_url(base_url: str | None, run_id: str) -> str | None:
    base = public_https(base_url)
    return f"{base}/#/runs/{quote(run_id, safe='')}" if base else None
