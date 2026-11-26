"""Slack integration for SentinelPR — posts analysis results to Slack channels
and handles interactive button callbacks.

Setup:
  SLACK_BOT_TOKEN   xoxb-... token from your Slack App (Bot Token)
  SLACK_SIGNING_SECRET  for verifying webhook payloads
  SLACK_CHANNEL     default channel ID or name (e.g. #sentinel-pr or C0XXXXXXXX)

The integration uses Slack's Block Kit for rich formatting.
Interactive callbacks require your server to be reachable at /slack/interactive.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# Decision colours for Slack (sidebar colour)
DECISION_COLOR = {
    "PASS": "#36a64f", "PASS_LOW": "#36a64f", "PASS_MEDIUM": "#2eb886", "PASS_HIGH": "#daa520",
    "CANARY": "#daa520", "CANARY_LOW": "#7ecef4", "CANARY_MEDIUM": "#daa520", "CANARY_HIGH": "#e8812d",
    "BLOCK": "#e01e5a", "BLOCK_LOW": "#e8812d", "BLOCK_MEDIUM": "#e01e5a", "BLOCK_HIGH": "#c0392b",
}

DECISION_EMOJI = {
    "PASS": ":white_check_mark:", "PASS_LOW": ":white_check_mark:", "PASS_MEDIUM": ":white_check_mark:", "PASS_HIGH": ":warning:",
    "CANARY": ":large_yellow_circle:", "CANARY_LOW": ":large_blue_circle:", "CANARY_MEDIUM": ":large_yellow_circle:", "CANARY_HIGH": ":large_orange_circle:",
    "BLOCK": ":no_entry:", "BLOCK_LOW": ":red_circle:", "BLOCK_MEDIUM": ":no_entry:", "BLOCK_HIGH": ":rotating_light:",
}

RISK_LABEL = {
    "PASS_LOW": "Very Low Risk", "PASS_MEDIUM": "Low-Medium Risk", "PASS_HIGH": "Borderline Safe",
    "CANARY_LOW": "Low Canary Risk", "CANARY_MEDIUM": "Moderate Canary Risk", "CANARY_HIGH": "High Canary Risk",
    "BLOCK_LOW": "Borderline Block", "BLOCK_MEDIUM": "Significant Risk", "BLOCK_HIGH": "Critical Risk",
    "PASS": "Safe", "CANARY": "Caution", "BLOCK": "High Risk",
}

LEVEL_EMOJI = {
    "info": ":information_source:",
    "warning": ":warning:",
    "error": ":red_circle:",
}


def is_configured() -> bool:
    """Returns True if SLACK_BOT_TOKEN and SLACK_CHANNEL are both set."""
    return bool(os.getenv("SLACK_BOT_TOKEN")) and bool(os.getenv("SLACK_CHANNEL"))


def _post(endpoint: str, payload: dict) -> dict:
    """Make an authenticated POST to https://slack.com/api/{endpoint}.

    Uses urllib.request (no extra deps). Reads SLACK_BOT_TOKEN from env.
    Returns the parsed JSON response. Raises on HTTP error or Slack API error.
    """
    token = os.getenv("SLACK_BOT_TOKEN", "")
    url = f"https://slack.com/api/{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    result: dict = json.loads(raw.decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Slack API error ({endpoint}): {result.get('error', 'unknown')}")
    return result


def build_blocks(report: dict, dashboard_url: str | None = None) -> list[dict]:
    """Build Slack Block Kit blocks for a PR analysis report.

    Returns a list of Block Kit block dicts ready to be passed to chat.postMessage.
    """
    decision: str = report.get("decision") or "UNKNOWN"
    emoji = DECISION_EMOJI.get(decision, ":grey_question:")
    risk: float | None = report.get("risk")
    risk_str = f"{risk:.3f}" if risk is not None else "N/A"
    verdict_label = RISK_LABEL.get(decision, decision)

    pr = report.get("pr") or {}
    pr_number = pr.get("number") or pr.get("ref") or "?"
    pr_title = pr.get("title") or "(no title)"
    pr_author = pr.get("author") or "unknown"
    repo_name = report.get("repository") or report.get("repo") or report.get("repo_name") or "unknown/repo"
    run_id = report.get("run_id") or ""
    decision_reason = report.get("decision_reason") or ""
    elapsed_s = report.get("elapsed_s")
    elapsed_str = f"{elapsed_s:.1f}s" if elapsed_s is not None else "N/A"

    verification = report.get("verification") or {}
    tests = verification.get("tests") or {}
    tests_passed = tests.get("passed")
    if isinstance(tests_passed, list):
        tests_passed = len(tests_passed)
    tests_failed_list = tests.get("failed") or []
    tests_failed_count = len(tests_failed_list)
    changed_lines = verification.get("changed_lines") or {}
    coverage_raw = changed_lines.get("coverage")
    if coverage_raw is not None:
        coverage_str = f"{float(coverage_raw) * 100:.1f}%"
    else:
        coverage_pct = (verification.get("coverage") or {}).get("percent")
        coverage_str = f"{coverage_pct:.1f}%" if coverage_pct is not None else "N/A"
    mutation = verification.get("mutation") or {}
    mutation_score = mutation.get("score")
    mutation_str = f"{mutation_score:.0%}" if mutation_score is not None else "N/A"
    mutants_list = mutation.get("mutants") or []
    survived_mutants = (
        sum(1 for m in mutants_list if isinstance(m, dict) and m.get("status") == "survived")
        if mutants_list
        else (mutation.get("survived") or 0)
    )

    contributions: list[dict] = report.get("contributions") or []

    blocks: list[dict] = []

    # ── Header ──────────────────────────────────────────────────────────────
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} SentinelPR — {decision} | PR #{pr_number}: {pr_title}",
            "emoji": True,
        },
    })

    # ── Context: repo / author / run_id ─────────────────────────────────────
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f":file_folder: *{repo_name}*"},
            {"type": "mrkdwn", "text": f":bust_in_silhouette: {pr_author}"},
            {"type": "mrkdwn", "text": f":label: `{run_id}`"},
        ],
    })

    # ── Main section: risk score + verdict ───────────────────────────────────
    verdict_text = (
        f"*Risk Score:* `{risk_str}` — {verdict_label}\n"
        f"*Decision:* {decision}"
    )
    if decision_reason:
        verdict_text += f"\n*Reason:* {decision_reason}"

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": verdict_text},
    })

    # ── Two-column fields: metrics ───────────────────────────────────────────
    fields: list[dict] = []
    if tests_passed is not None or tests_failed_count is not None:
        pass_str = str(tests_passed) if tests_passed is not None else "?"
        fields.append({"type": "mrkdwn", "text": f"*Tests:* {pass_str} pass / {tests_failed_count} fail"})
    fields.append({"type": "mrkdwn", "text": f"*Coverage:* {coverage_str}"})
    fields.append({"type": "mrkdwn", "text": f"*Mutation:* {mutation_str}"})
    fields.append({"type": "mrkdwn", "text": f"*Elapsed:* {elapsed_str}"})

    if fields:
        blocks.append({"type": "section", "fields": fields})

    # ── Verified test failures (up to 3) ────────────────────────────────────
    if tests_failed_list:
        shown = tests_failed_list[:3]
        lines = ["*:x: Failing tests:*"]
        for f in shown:
            node_id = f.get("nodeid", f) if isinstance(f, dict) else str(f)
            lines.append(f"  • `{node_id}`")
        if tests_failed_count > 3:
            lines.append(f"  _…and {tests_failed_count - 3} more_")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    # ── Surviving mutants ────────────────────────────────────────────────────
    if survived_mutants:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":dna: *{survived_mutants} mutant(s) survived* — mutation testing found untested code paths."},
        })

    # ── Top 3 risk contributors ──────────────────────────────────────────────
    if contributions:
        top3 = contributions[:3]
        lines = ["*:bar_chart: Top risk contributors:*"]
        for c in top3:
            feature = c.get("feature") or c.get("name") or "?"
            weight = c.get("weight") or c.get("contribution") or c.get("value")
            w_str = f"{weight:+.3f}" if isinstance(weight, (int, float)) else str(weight)
            lines.append(f"  • `{feature}` → {w_str}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})

    # ── Divider ──────────────────────────────────────────────────────────────
    blocks.append({"type": "divider"})

    # ── Action buttons ───────────────────────────────────────────────────────
    actions: list[dict] = []

    # "✅ Approve" when decision starts with PASS or CANARY
    if decision.startswith("PASS") or decision.startswith("CANARY"):
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "✅ Approve", "emoji": True},
            "style": "primary",
            "action_id": "sentinel_approve",
            "value": run_id,
        })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "⛔ Request Changes", "emoji": True},
        "style": "danger",
        "action_id": "sentinel_reject",
        "value": run_id,
    })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "🔄 Rerun", "emoji": True},
        "action_id": "sentinel_rerun",
        "value": run_id,
    })

    actions.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "❓ Ask", "emoji": True},
        "action_id": "sentinel_ask",
        "value": run_id,
    })

    if dashboard_url:
        report_url = f"{dashboard_url.rstrip('/')}/#/runs/{run_id}" if run_id else dashboard_url
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "🔗 View Full Report", "emoji": True},
            "url": report_url,
            "action_id": "sentinel_view_report",
        })

    blocks.append({"type": "actions", "elements": actions})

    # ── Footer context with dashboard link ───────────────────────────────────
    if dashboard_url:
        report_url = f"{dashboard_url.rstrip('/')}/#/runs/{run_id}" if run_id else dashboard_url
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":link: <{report_url}|View full report on SentinelPR>"},
            ],
        })

    return blocks


def post_analysis(
    report: dict,
    *,
    channel: str | None = None,
    dashboard_url: str | None = None,
    thread_ts: str | None = None,
) -> dict | None:
    """Post a PR analysis result to Slack as a rich Block Kit message.

    Returns the Slack API response dict (which includes ``ts`` for threading),
    or None if Slack is not configured. Logs errors but does not raise.
    """
    if not is_configured():
        log.debug("Slack not configured; skipping post_analysis.")
        return None

    target_channel = channel or os.getenv("SLACK_CHANNEL", "")
    decision = report.get("decision") or "UNKNOWN"
    color = DECISION_COLOR.get(decision, "#cccccc")

    try:
        blocks = build_blocks(report, dashboard_url=dashboard_url)
        fallback = format_verdict_text(report)
        payload: dict[str, Any] = {
            "channel": target_channel,
            "text": fallback,  # fallback for notifications / accessibility
            "attachments": [
                {
                    "color": color,
                    "blocks": blocks,
                    "fallback": fallback,
                }
            ],
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        result = _post("chat.postMessage", payload)
        log.info("Slack analysis posted to %s (ts=%s)", target_channel, result.get("ts"))
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to post Slack analysis: %s", exc)
        return None


def post_notification(
    message: str,
    *,
    channel: str | None = None,
    level: str = "info",
) -> dict | None:
    """Post a simple text notification to Slack.

    ``level`` can be ``"info"``, ``"warning"``, or ``"error"``.
    Returns the Slack API response or None if not configured / on error.
    """
    if not is_configured():
        log.debug("Slack not configured; skipping post_notification.")
        return None

    target_channel = channel or os.getenv("SLACK_CHANNEL", "")
    icon = LEVEL_EMOJI.get(level, ":information_source:")
    text = f"{icon} {message}"

    try:
        result = _post("chat.postMessage", {"channel": target_channel, "text": text})
        log.info("Slack notification posted to %s", target_channel)
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to post Slack notification: %s", exc)
        return None


def verify_slack_signature(body_bytes: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack's request signature using HMAC-SHA256 and SLACK_SIGNING_SECRET.

    Returns False if the secret is not configured or the signature does not match.
    See https://api.slack.com/authentication/verifying-requests-from-slack
    """
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        log.warning("SLACK_SIGNING_SECRET not configured; cannot verify Slack signature.")
        return False

    base_string = f"v0:{timestamp}:".encode("utf-8") + body_bytes
    computed = "v0=" + hmac.new(
        secret.encode("utf-8"),
        base_string,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def handle_interactive_payload(payload: dict) -> dict:
    """Handle an interactive button click from Slack.

    Supported action_ids:
      - ``sentinel_approve`` → ``{"action": "approve", "run_id": value}``
      - ``sentinel_rerun``   → ``{"action": "rerun",   "run_id": value}``
      - ``sentinel_ask``     → ``{"action": "ask",     "run_id": value}``

    Logs the action and the user who clicked. Unknown actions are returned with
    ``action="unknown"`` so callers can handle them gracefully.
    """
    actions: list[dict] = payload.get("actions") or []
    user_info = payload.get("user") or {}
    user = user_info.get("name") or user_info.get("id") or "unknown"

    if not actions:
        log.warning("Slack interactive payload with no actions from user=%s", user)
        return {"action": "unknown", "run_id": ""}

    # We handle the first action in the list (Slack sends one per click).
    action = actions[0]
    action_id: str = action.get("action_id", "")
    value: str = action.get("value") or ""

    _ACTION_MAP = {
        "sentinel_approve": "approve",
        "sentinel_reject": "reject",
        "sentinel_rerun": "rerun",
        "sentinel_ask": "ask",
    }

    action_name = _ACTION_MAP.get(action_id, "unknown")
    log.info("Slack interactive: action=%s run_id=%s user=%s", action_name, value, user)
    return {"action": action_name, "run_id": value, "user": user}


def format_verdict_text(report: dict) -> str:
    """Return a plain-text one-line summary of a report.

    Format: ``[DECISION] PR #X (risk 0.89) — repo/name``
    Used as the notification fallback for Slack messages.
    """
    decision = report.get("decision") or "UNKNOWN"
    pr = report.get("pr") or {}
    pr_ref = pr.get("number") or pr.get("ref") or "?"
    risk = report.get("risk")
    risk_str = f"{risk:.2f}" if risk is not None else "?"
    repo = report.get("repository") or report.get("repo") or report.get("repo_name") or "unknown/repo"
    return f"[{decision}] PR #{pr_ref} (risk {risk_str}) — {repo}"
