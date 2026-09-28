"""Render a run report as a PR comment, a Check Run summary and diff annotations."""

from __future__ import annotations

from collections import Counter

ICON = {"PASS": "✅", "CANARY": "🟡", "BLOCK": "⛔"}
CONCLUSION = {"PASS": "success", "CANARY": "neutral", "BLOCK": "failure"}
ROLLOUT = {
    "PASS": "merge allowed, normal release",
    "CANARY": "merge allowed, deploy via canary (10% → 25% → 50% → 100%)",
    "BLOCK": "merge blocked until the issues below are addressed",
}
MAX_ANNOTATIONS = 50


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _short(unit_id: str) -> str:
    path, _, name = unit_id.partition("::")
    return f"{path}::{name}" if name else path


def headline(report: dict) -> str:
    decision = report["decision"]
    if report.get("rejected"):
        return f"## 🛡️ SentinelPR — {ICON[decision]} {decision} (analysis refused)"
    calibrated = "calibrated" if report.get("calibrated") else "prior model"
    return f"## 🛡️ SentinelPR — {ICON[decision]} {decision} (risk {report['risk']:.2f}, {calibrated})"


def summary_lines(report: dict) -> list[str]:
    if report.get("rejected"):
        return [f"**Why:** {report['decision_reason']}", "", "Nothing from this PR was sent to a language model."]
    claims = report.get("claims", [])
    verified_impact = [c for c in claims if c["status"] == "VERIFIED" and c["type"] in ("module_impact", "api_break")]
    refuted = sum(1 for c in claims if c["status"] == "REFUTED")
    verification = report.get("verification") or {}
    tests = verification.get("tests", {})
    mutation = verification.get("mutation", {})
    changed = verification.get("changed_lines", {})
    selection = report.get("selection") or {}
    lines = []

    impacted = ", ".join(f"`{_short(c['target'])}`" for c in verified_impact[:5]) or "no other modules"
    lines.append(f"**Impacted (verified):** {impacted} · refuted: {refuted} claim(s)")

    failed = tests.get("failed", [])
    test_status = "all pass" if not failed and not tests.get("collection_error") else f"**{len(failed)} failing**"
    saved = selection.get("time_saved_ratio")
    saved_text = f" ({saved:.0%} of suite time saved)" if saved else ""
    score = mutation.get("score")
    warn = " ⚠️" if score is not None and score < 0.6 else ""
    lines.append(
        f"**Tests:** {tests.get('selected', 0)}/{selection.get('total_available', 0)} selected{saved_text} — {test_status} · "
        f"changed lines executed: {_pct(changed.get('coverage'))} · mutation score on changed lines: {_pct(score)}{warn}"
    )

    bug_links = [c for c in claims if c["type"] == "history_link" and c["status"] == "VERIFIED"]
    if bug_links:
        lines.append("**History:** " + "; ".join(c["assertion"] for c in bug_links[:3]))
    memories = (report.get("history") or {}).get("memories") or []
    if memories and not bug_links:
        lines.append("**History:** " + memories[0])

    factors = [c for c in report.get("contributions", []) if abs(c["contribution"]) >= 0.05][:3]
    if factors:
        lines.append("**Why this score:** " + ", ".join(f"{c['label']} ({c['contribution']:+.2f})" for c in factors))
    if report.get("explanation_source") == "llm" and report.get("explanation"):
        lines.append(f"> {report['explanation']}")
    guards = Counter(e["guard"] for e in report.get("guard_events", []) if e["severity"] != "info")
    if guards:
        lines.append("**Guardrails:** " + ", ".join(f"{k} ×{v}" for k, v in guards.items()))
    lines.append(f"**Decision:** {ROLLOUT[report['decision']]} — {report.get('decision_reason', '')}")
    return lines


def details(report: dict) -> str:
    parts = []
    claims = report.get("claims", [])
    if claims:
        rows = ["| claim | type | target | status | why |", "|---|---|---|---|---|"]
        verdicts = {v["claim_id"]: v for v in report.get("verdicts", [])}
        for c in claims[:40]:
            detail = verdicts.get(c["claim_id"], {}).get("detail", "")
            rows.append(f"| {c['claim_id']} | {c['type']} | `{c['target'][:70]}` | {c['status']} | {detail[:90]} |")
        parts.append("<details><summary>Claims and verdicts</summary>\n\n" + "\n".join(rows) + "\n\n</details>")
    mutants = ((report.get("verification") or {}).get("mutation") or {}).get("mutants", [])
    if mutants:
        rows = ["| line | operator | mutation | result |", "|---|---|---|---|"]
        for m in mutants:
            rows.append(f"| `{m['file']}#L{m['line']}` | {m['operator']} | `{m['original'][:40]}` → `{m['mutated'][:40]}` | {m['status']} |")
        parts.append("<details><summary>Mutants on changed lines</summary>\n\n" + "\n".join(rows) + "\n\n</details>")
    trace = report.get("trace", [])
    if trace:
        rows = ["| node | time | tokens | model | note |", "|---|---|---|---|---|"]
        for t in trace:
            rows.append(f"| {t['node']} | {t['end'] - t['start']:.2f}s | {t['tokens']} | {t.get('model') or '—'} | {t.get('note', '')[:80]} |")
        parts.append("<details><summary>Pipeline trace</summary>\n\n" + "\n".join(rows) + "\n\n</details>")
    return "\n\n".join(parts)


def render_comment(report: dict, marker: str, dashboard_url: str | None = None) -> str:
    body = [marker, headline(report), "", *[f"{line}  " for line in summary_lines(report)], ""]
    extra = details(report)
    if extra:
        body += [extra, ""]
    footer = f"<sub>run `{report['run_id']}` · {report.get('elapsed_s', 0):.0f}s · {report.get('tokens', 0)} LLM tokens"
    if dashboard_url:
        footer += f" · [Full evidence & pipeline trace →]({dashboard_url.rstrip('/')}/#/runs/{report['run_id']})"
    body.append(footer + "</sub>")
    return "\n".join(body)


def annotations(report: dict, prefix: str = "") -> list[dict]:
    """GitHub Check Run annotations on the changed lines."""
    out: list[dict] = []
    verification = report.get("verification") or {}
    for ref in (verification.get("changed_lines") or {}).get("uncovered", []):
        path, _, line = ref.rpartition("#L")
        out.append({"path": prefix + path, "start_line": int(line), "end_line": int(line), "annotation_level": "warning",
                    "title": "Changed line not executed by any test", "message": "No selected test executes this line on the PR head."})
    for m in (verification.get("mutation") or {}).get("mutants", []):
        if m["status"] == "survived":
            out.append({"path": prefix + m["file"], "start_line": m["line"], "end_line": m["line"], "annotation_level": "warning",
                        "title": "Mutant survived here",
                        "message": f"Changing `{m['original']}` to `{m['mutated']}` does not make any test fail."})
    units = {u["id"]: u for u in report.get("change_units", [])}
    for c in report.get("claims", []):
        if c["type"] == "history_link" and c["status"] == "VERIFIED" and c["target"] in units:
            unit = units[c["target"]]
            line = (unit.get("new_range") or unit.get("old_range") or [1])[0]
            out.append({"path": prefix + unit["file"], "start_line": line, "end_line": line, "annotation_level": "notice",
                        "title": "Function linked to a past bug", "message": c["assertion"]})
    return out[:MAX_ANNOTATIONS]


def check_run_output(report: dict, prefix: str = "") -> dict:
    title = f"{report['decision']}" + ("" if report.get("risk") is None else f" — risk {report['risk']:.2f}")
    return {
        "title": title,
        "summary": "\n\n".join(summary_lines(report)),
        "text": details(report)[:60000],
        "annotations": annotations(report, prefix),
    }
