"""Compact, JSON-safe summaries of what each pipeline stage produced.

These are attached to the stage's completion event so the execution inspector can show real
inputs and outputs while a run is still going. They only read results the stage already
computed; nothing is recomputed or invented here.
"""

from __future__ import annotations

CAP = 40


def _claims(claims) -> list[dict]:
    return [{"claim_id": c.claim_id, "type": c.type, "target": c.target, "reason": c.reason[:200], "evidence_ids": c.evidence_ids[:6]}
            for c in claims[:CAP]]


def stage_detail(name: str, rt) -> dict:
    """Detail for a finished pipeline node. ``rt`` is the pipeline's ``Runtime``."""
    try:
        return _DETAIL.get(name, lambda _rt: {})(rt) or {}
    except Exception as exc:  # observability must never break a run
        return {"detail_error": f"{type(exc).__name__}: {exc}"}


def _guard_in(rt):
    g = rt.guard
    if g is None:
        return {}
    return {"rejected": g.rejected, "reason": g.reject_reason, "secrets_found": g.secrets_found,
            "injection_attempts": g.injection_attempts,
            "events": [{"guard": e.guard, "severity": e.severity, "message": e.message[:200], "location": e.location} for e in g.events[:CAP]]}


def _parse_diff(rt):
    ctx = rt.ctx
    return {"files": [{"path": c.path, "status": c.status, "added": c.additions, "removed": c.deletions} for c in ctx.changes[:CAP]],
            "units": [{"id": u.id, "kind": u.kind, "change_type": u.change_type, "changed_lines": len(u.changed_lines),
                       "signature_changed": u.signature_changed} for u in ctx.units[:CAP]],
            "diff_lines": ctx.diff_lines}


def _impact(rt):
    r = rt.impact
    if r is None:
        return {}
    by_type: dict[str, int] = {}
    for c in r.claims:
        by_type[c.type] = by_type.get(c.type, 0) + 1
    return {"mode": r.mode, "claims_by_type": by_type, "claims": _claims(r.claims), "llm_risk": r.llm_risk,
            "llm_risk_reason": r.llm_risk_reason[:300], "discarded_claims": r.discarded[:10]}


def _historian(rt):
    h = rt.history
    if h is None:
        return {}
    return {"claims": _claims(h.claims), "memories": [str(m)[:240] for m in h.memories[:10]], "features": h.features}


def _signals(rt):
    s = rt.signals
    if s is None:
        return {}
    return {"intent": s.intent, "intent_source": s.intent_source, "features": s.features}


def _select(rt):
    s = rt.selection
    if s is None:
        return {}
    return {"selected": s.test_ids[:CAP], "selected_count": len(s.test_ids), "total_available": s.total_available,
            "reasons": {k: v for k, v in list(s.reasons.items())[:CAP]},
            "estimated_s": s.estimated_s, "full_suite_s": s.full_suite_s, "time_saved_ratio": round(s.time_saved_ratio, 4)}


def _verify(rt):
    v = rt.verification
    if v is None:
        return {}
    mutants = v.mutation.mutants
    return {"tests": {"selected": len(v.test_run.selected), "passed": len(v.test_run.passed), "failed": [o.nodeid for o in v.test_run.failed][:20],
                      "duration_s": v.test_run.duration_s, "collection_error": (v.test_run.collection_error or "")[:500] or None},
            "changed_lines": {"total": v.changed_lines_total, "covered": v.changed_lines_covered, "uncovered": [f"{f}#L{n}" for f, n in v.uncovered[:20]]},
            "mutation": {"score": v.mutation.score, "mutants": len(mutants), "killed": sum(m.status == "killed" for m in mutants),
                         "survived": sum(m.status == "survived" for m in mutants), "duration_s": v.mutation.duration_s},
            "verdicts": {s: sum(1 for x in v.verdicts if x.status.value == s) for s in ("VERIFIED", "REFUTED", "UNVERIFIABLE")}}


def _reconcile(rt):
    r = rt.reconciliation
    if r is None:
        return {}
    return {"counts": r.counts, "impact_refuted_ratio": r.impact_refuted_ratio, "should_retry_impact": r.should_retry_impact}


def _retry(rt):
    return {"revised_claims": _claims(rt.retry_claims)}


def _score(rt):
    a = rt.assessment
    if a is None:
        return {}
    return {"risk": a.probability, "model": a.model, "calibrated": a.calibrated,
            "contributions": [c.model_dump() for c in a.contributions], "features": a.features}


def _decide(rt):
    p = rt.policy
    if p is None:
        return {}
    return {"decision": p.decision, "reason": p.reason, "thresholds": p.thresholds, "explanation_source": rt.explanation_source}


def _guard_out(rt):
    return {"decision_after_integrity_check": rt.policy.decision if rt.policy else None}


_DETAIL = {
    "guard_in": _guard_in, "parse_diff": _parse_diff, "impact": _impact, "historian": _historian, "signals": _signals,
    "select_tests": _select, "verify": _verify, "reconcile": _reconcile, "impact_retry": _retry, "score": _score,
    "decide": _decide, "guard_out": _guard_out,
}
