"""Notion integration for SentinelPR — logs PR analysis results to a Notion database.

Each analysis creates a new page in your Notion database with:
  - PR title, number, repository, author
  - Decision (PASS/CANARY/BLOCK with sub-category)
  - Risk score and calibration status
  - Test results, coverage, mutation score
  - Top risk contributors
  - Claims and verification summary
  - Link back to dashboard

Setup:
  NOTION_API_KEY     your Notion integration token (secret_...)
  NOTION_DATABASE_ID the ID of your Notion database (from the URL)

Notion database must have these properties (create them manually or use the
  setup guide in docs/notion.md):
  - Name (title) — auto-created
  - Decision (select)
  - Risk Score (number)
  - Repository (text)
  - PR Number (number)
  - Author (text)
  - Base Decision (select): PASS / CANARY / BLOCK
  - Risk Level (select): LOW / MEDIUM / HIGH
  - Status (select): analysing / completed / failed
  - Run ID (text)
  - Analysed At (date)
  - Tests Passed (number)
  - Tests Failed (number)
  - Coverage (number, 0-100)
  - Mutation Score (number, 0-100)
  - Elapsed Seconds (number)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("sentinel.publish.notion")

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

# Decision → callout emoji mapping
_DECISION_EMOJI = {
    "PASS": "✅",
    "PASS_LOW": "✅",
    "PASS_MEDIUM": "✅",
    "PASS_HIGH": "⚠️",
    "CANARY": "🟡",
    "CANARY_LOW": "🟡",
    "CANARY_MEDIUM": "🟡",
    "CANARY_HIGH": "🟠",
    "BLOCK": "⛔",
    "BLOCK_LOW": "🔴",
    "BLOCK_MEDIUM": "⛔",
    "BLOCK_HIGH": "🚫",
}

# Decision → callout background colour
_CALLOUT_COLOUR = {
    "PASS": "green",
    "PASS_LOW": "green",
    "PASS_MEDIUM": "green",
    "PASS_HIGH": "yellow",
    "CANARY": "yellow",
    "CANARY_LOW": "blue",
    "CANARY_MEDIUM": "yellow",
    "CANARY_HIGH": "orange",
    "BLOCK": "red",
    "BLOCK_LOW": "yellow",
    "BLOCK_MEDIUM": "red",
    "BLOCK_HIGH": "red",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """Return True if both NOTION_API_KEY and NOTION_DATABASE_ID are set."""
    return bool(os.getenv("NOTION_API_KEY") and os.getenv("NOTION_DATABASE_ID"))


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------


def _notion_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated request to the Notion API.

    Args:
        method: HTTP method (GET, POST, PATCH, …).
        path: API path, relative to https://api.notion.com/v1/ (e.g. "pages").
        body: Optional JSON-serialisable request body.

    Returns:
        Parsed JSON response as a dict.

    Raises:
        urllib.error.HTTPError: on a non-2xx response.
        ValueError: if the response body is not valid JSON.
    """
    api_key = os.getenv("NOTION_API_KEY", "")
    url = f"{_NOTION_API_BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 — controlled URL
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Notion property constructors
# ---------------------------------------------------------------------------


def _rich_text(text: str) -> list[dict]:
    """Return a Notion rich_text array for a plain string."""
    return [{"type": "text", "text": {"content": str(text)[:2000]}}]


def _select(name: str) -> dict:
    """Return a Notion select property value."""
    return {"select": {"name": str(name)[:100]}}


def _number(value: float | None) -> dict:
    """Return a Notion number property value (None is allowed)."""
    return {"number": value}


# ---------------------------------------------------------------------------
# Build page properties
# ---------------------------------------------------------------------------


def build_page_properties(report: dict) -> dict:
    """Build the Notion database page-properties dict from a SentinelPR report.

    Every field extraction is wrapped in its own try/except so that a missing
    or malformed field never prevents the rest of the properties from being set.

    Args:
        report: The full SentinelPR analysis report dict.

    Returns:
        A dict suitable for the ``properties`` key of a Notion page-create request.
    """
    props: dict[str, Any] = {}

    # --- Name (title) -------------------------------------------------------
    try:
        pr = report["pr"]
        pr_number = pr.get("number") or ""
        pr_title = pr.get("title") or ""
        repo_name = report.get("repository") or report.get("repo_name", "")
        title_text = f"PR #{pr_number}: {pr_title} — {repo_name}"
        props["Name"] = {"title": _rich_text(title_text)}
    except Exception:
        props["Name"] = {"title": _rich_text("SentinelPR Analysis")}

    # --- Decision -----------------------------------------------------------
    try:
        decision = report["decision"]
        if decision:
            props["Decision"] = _select(decision)
    except Exception:
        pass

    # --- Base Decision & Risk Level -----------------------------------------
    try:
        decision = report.get("decision") or ""
        parts = decision.split("_", 1)
        base = parts[0] if parts else decision
        level = parts[1] if len(parts) > 1 else "STANDARD"
        if base:
            props["Base Decision"] = _select(base)
        if level:
            props["Risk Level"] = _select(level)
    except Exception:
        pass

    # --- Risk Score ---------------------------------------------------------
    try:
        risk_raw = report["risk"]
        if risk_raw is not None:
            props["Risk Score"] = _number(round(float(risk_raw) * 100, 1))
    except Exception:
        props["Risk Score"] = _number(None)

    # --- Repository ---------------------------------------------------------
    try:
        props["Repository"] = {"rich_text": _rich_text(report.get("repository") or report.get("repo_name", ""))}
    except Exception:
        pass

    # --- PR Number ----------------------------------------------------------
    try:
        pr_num = report["pr"]["number"]
        if pr_num is not None:
            props["PR Number"] = _number(int(pr_num))
    except Exception:
        props["PR Number"] = _number(None)

    # --- Author -------------------------------------------------------------
    try:
        author = report["pr"].get("author") or ""
        props["Author"] = {"rich_text": _rich_text(author)}
    except Exception:
        pass

    # --- Status -------------------------------------------------------------
    try:
        status = "completed" if report.get("decision") else "failed"
        props["Status"] = _select(status)
    except Exception:
        props["Status"] = _select("failed")

    # --- Run ID -------------------------------------------------------------
    try:
        run_id = report.get("run_id") or ""
        props["Run ID"] = {"rich_text": _rich_text(run_id)}
    except Exception:
        pass

    # --- Analysed At --------------------------------------------------------
    try:
        created_at = report.get("created_at")
        if created_at:
            # Accept both float timestamps and ISO strings
            if isinstance(created_at, (int, float)):
                from datetime import datetime, timezone
                iso = datetime.fromtimestamp(float(created_at), tz=timezone.utc).isoformat()
            else:
                iso = str(created_at)
            props["Analysed At"] = {"date": {"start": iso}}
    except Exception:
        pass

    # --- Tests Passed -------------------------------------------------------
    try:
        verification = report["verification"]
        tests = verification["tests"]
        passed_count = tests.get("passed_count")
        if passed_count is None:
            passed_list = tests.get("passed") or []
            passed_count = len(passed_list)
        props["Tests Passed"] = _number(int(passed_count))
    except Exception:
        props["Tests Passed"] = _number(None)

    # --- Tests Failed -------------------------------------------------------
    try:
        verification = report["verification"]
        tests = verification["tests"]
        preexisting = set(tests.get("preexisting_failures") or [])
        all_failed = tests.get("failed") or []
        # Normalise: failed may be list of str or list of dicts with "nodeid"
        def _nodeid(f):
            return f["nodeid"] if isinstance(f, dict) else f
        new_failures = [f for f in all_failed if _nodeid(f) not in preexisting]
        props["Tests Failed"] = _number(len(new_failures))
    except Exception:
        props["Tests Failed"] = _number(None)

    # --- Coverage -----------------------------------------------------------
    try:
        coverage_raw = report["verification"]["changed_lines"]["coverage"]
        if coverage_raw is not None:
            props["Coverage"] = _number(round(float(coverage_raw) * 100, 1))
        else:
            props["Coverage"] = _number(None)
    except Exception:
        props["Coverage"] = _number(None)

    # --- Mutation Score -----------------------------------------------------
    try:
        mutation_raw = report["verification"]["mutation"]["score"]
        if mutation_raw is not None:
            props["Mutation Score"] = _number(round(float(mutation_raw) * 100, 1))
        else:
            props["Mutation Score"] = _number(None)
    except Exception:
        props["Mutation Score"] = _number(None)

    # --- Elapsed Seconds ----------------------------------------------------
    try:
        elapsed = report.get("elapsed_s")
        if elapsed is not None:
            props["Elapsed Seconds"] = _number(round(float(elapsed), 2))
        else:
            props["Elapsed Seconds"] = _number(None)
    except Exception:
        props["Elapsed Seconds"] = _number(None)

    return props


# ---------------------------------------------------------------------------
# Build page content (body blocks)
# ---------------------------------------------------------------------------

def _heading2(text: str) -> dict:
    """Return a Notion heading_2 block."""
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rich_text(text)}}


def _bullet(text: str) -> dict:
    """Return a Notion bulleted_list_item block."""
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich_text(text)}}


def _paragraph(text: str) -> dict:
    """Return a Notion paragraph block."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}


def _divider() -> dict:
    """Return a Notion divider block."""
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(text: str, emoji: str = "📋", colour: str = "gray") -> dict:
    """Return a Notion callout block."""
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": f"{colour}_background",
        },
    }


def _code_block(text: str, language: str = "plain text") -> dict:
    """Return a Notion code block."""
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": _rich_text(text[:2000]),
            "language": language,
        },
    }


def build_page_content(report: dict, dashboard_url: str | None = None) -> list[dict]:
    """Build the Notion page body blocks from a SentinelPR report.

    Args:
        report: The full SentinelPR analysis report dict.
        dashboard_url: Optional base URL of the SentinelPR dashboard (used to
            construct a deep link to the run).

    Returns:
        A list of Notion block dicts suitable for the ``children`` key of a
        page-create request.
    """
    blocks: list[dict] = []

    # 1. Callout — decision + reason -----------------------------------------
    try:
        decision = report.get("decision") or "UNKNOWN"
        reason = report.get("decision_reason") or ""
        emoji = _DECISION_EMOJI.get(decision, "📋")
        colour = _CALLOUT_COLOUR.get(decision, "gray")
        callout_text = f"{decision}"
        if reason:
            callout_text += f" — {reason[:400]}"
        blocks.append(_callout(callout_text, emoji=emoji, colour=colour))
    except Exception:
        pass

    # 2–3. Risk Analysis section ---------------------------------------------
    try:
        blocks.append(_heading2("📊 Risk Analysis"))
        risk = report.get("risk")
        risk_pct = f"{risk * 100:.1f}%" if risk is not None else "n/a"
        calibrated_label = "calibrated model" if report.get("calibrated") else "prior model"
        blocks.append(_bullet(f"Risk score: {risk_pct} ({calibrated_label})"))

        contributions = report.get("contributions") or []
        top3 = [c for c in contributions if abs(c.get("contribution", 0)) >= 0.01][:3]
        for c in top3:
            label = c.get("label") or c.get("feature") or "unknown"
            contrib = c.get("contribution", 0)
            sign = "+" if contrib >= 0 else ""
            blocks.append(_bullet(f"  · {label}: {sign}{contrib:.3f}"))
        if not top3:
            blocks.append(_bullet("No significant contributors recorded."))
    except Exception:
        pass

    # 4–5. Test & Mutation Results -------------------------------------------
    try:
        blocks.append(_heading2("🧪 Test & Mutation Results"))
        verification = report.get("verification") or {}
        tests = verification.get("tests") or {}
        selection = report.get("selection") or {}

        selected = tests.get("selected", 0) or selection.get("selected", 0)
        total = selection.get("total_available", 0)

        preexisting = set(tests.get("preexisting_failures") or [])
        all_failed = tests.get("failed") or []
        def _nodeid(f):
            return f["nodeid"] if isinstance(f, dict) else f
        new_failures = [f for f in all_failed if _nodeid(f) not in preexisting]

        passed_count = tests.get("passed_count")
        if passed_count is None:
            passed_count = len(tests.get("passed") or [])

        blocks.append(_bullet(f"Tests selected: {selected}/{total}"))
        blocks.append(_bullet(f"Passed: {passed_count} · Failed (new): {len(new_failures)}"))

        changed = verification.get("changed_lines") or {}
        coverage = changed.get("coverage")
        coverage_str = f"{coverage * 100:.1f}%" if coverage is not None else "n/a"
        blocks.append(_bullet(f"Changed-line coverage: {coverage_str}"))

        mutation = verification.get("mutation") or {}
        score = mutation.get("score")
        score_str = f"{score * 100:.1f}%" if score is not None else "n/a"
        warn = " ⚠️" if score is not None and score < 0.6 else ""
        blocks.append(_bullet(f"Mutation score (changed lines): {score_str}{warn}"))
    except Exception:
        pass

    # 6–7. Claims & Verification ---------------------------------------------
    try:
        blocks.append(_heading2("🔍 Claims & Verification"))
        claims = report.get("claims") or []
        verified = sum(1 for c in claims if c.get("status") == "VERIFIED")
        refuted = sum(1 for c in claims if c.get("status") == "REFUTED")
        unverifiable = sum(1 for c in claims if c.get("status") == "UNVERIFIABLE")
        unchecked = len(claims) - verified - refuted - unverifiable
        text = f"{verified} verified · {refuted} refuted · {unverifiable} unverifiable"
        if unchecked > 0:
            text += f" · {unchecked} unchecked"
        blocks.append(_paragraph(text))
    except Exception:
        pass

    # 8–9. Risk Factors (surviving mutants / uncovered lines) ----------------
    try:
        verification = report.get("verification") or {}
        mutation = verification.get("mutation") or {}
        mutants = mutation.get("mutants") or []
        survived = [m for m in mutants if m.get("status") == "survived"]
        changed = verification.get("changed_lines") or {}
        uncovered = changed.get("uncovered") or []

        if survived or uncovered:
            blocks.append(_heading2("⚠️ Risk Factors"))
            for m in survived[:5]:
                loc = f"{m.get('file', '?')}#L{m.get('line', '?')}"
                blocks.append(_bullet(
                    f"Surviving mutant at {loc}: `{str(m.get('original',''))[:40]}` → `{str(m.get('mutated',''))[:40]}`"
                ))
            if len(survived) > 5:
                blocks.append(_bullet(f"… and {len(survived) - 5} more surviving mutants."))
            for ref in uncovered[:5]:
                blocks.append(_bullet(f"Uncovered changed line: {ref}"))
            if len(uncovered) > 5:
                blocks.append(_bullet(f"… and {len(uncovered) - 5} more uncovered lines."))
    except Exception:
        pass

    # 10. Links --------------------------------------------------------------
    try:
        if dashboard_url:
            run_id = report.get("run_id") or ""
            full_url = f"{dashboard_url.rstrip('/')}/#/runs/{run_id}" if run_id else dashboard_url
            blocks.append(_heading2("🔗 Links"))
            # Notion bookmark blocks need a URL; fall back to paragraph when not set
            if full_url.startswith("http"):
                blocks.append({
                    "object": "block",
                    "type": "bookmark",
                    "bookmark": {"url": full_url},
                })
            else:
                blocks.append(_paragraph(full_url))
    except Exception:
        pass

    # 11. Divider ------------------------------------------------------------
    blocks.append(_divider())

    # 12. Code block — decision reason ---------------------------------------
    try:
        reason = report.get("decision_reason") or ""
        if reason:
            blocks.append(_code_block(reason, language="plain text"))
    except Exception:
        pass

    return blocks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_analysis_page(report: dict, *, dashboard_url: str | None = None) -> dict | None:
    """Create a new Notion page in the configured database for a PR analysis.

    Builds the page properties and body from *report*, then POSTs to the
    Notion ``/pages`` endpoint.  Any error is logged at WARNING level but
    never re-raised, so a Notion outage never breaks an analysis run.

    Args:
        report: The full SentinelPR analysis report dict.
        dashboard_url: Optional SentinelPR dashboard base URL.  When provided,
            a deep-link to the specific run is added to the page.

    Returns:
        The created Notion page dict (from the API response), or None on error.
    """
    if not is_configured():
        log.debug("Notion integration not configured; skipping page creation.")
        return None

    database_id = os.getenv("NOTION_DATABASE_ID", "")
    try:
        properties = build_page_properties(report)
        children = build_page_content(report, dashboard_url=dashboard_url)
        payload = {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": children,
        }
        page = _notion_request("POST", "pages", body=payload)
        log.info(
            "Notion page created for run %s: %s",
            report.get("run_id", "?"),
            page.get("url", page.get("id", "?")),
        )
        return page
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()
        except Exception:
            pass
        log.warning(
            "Notion page creation failed (HTTP %s) for run %s: %s",
            exc.code,
            report.get("run_id", "?"),
            body[:500] or str(exc),
        )
        return None
    except Exception as exc:
        log.warning(
            "Notion page creation failed for run %s: %s",
            report.get("run_id", "?"),
            exc,
        )
        return None


def update_page_status(page_id: str, status: str) -> dict | None:
    """Update the Status property of an existing Notion page.

    Useful for marking a page as "failed" after an error mid-run.

    Args:
        page_id: The Notion page ID returned by :func:`create_analysis_page`.
        status: The new status string (e.g. "failed", "completed").

    Returns:
        The updated Notion page dict, or None on error.
    """
    if not is_configured():
        return None
    try:
        payload = {"properties": {"Status": _select(status)}}
        return _notion_request("PATCH", f"pages/{page_id}", body=payload)
    except Exception as exc:
        log.warning("Notion page status update failed for page %s: %s", page_id, exc)
        return None
