"""'Why this score': a short explanation built from the model's own contributions.

A deterministic template is always produced. When an LLM is available it may rephrase the
top factors, but every sentence it writes must cite evidence ids from a fixed list and pass
the citation guard; anything else is dropped. The LLM never sees or changes the decision logic.
"""

from __future__ import annotations

import logging

from sentinel.agents.prompts import load_prompt
from sentinel.guards.output import enforce_citations
from sentinel.models import GuardEvent, RiskAssessment

log = logging.getLogger(__name__)


def feature_evidence(name: str) -> str:
    return f"feat:{name}"


def template_explanation(assessment: RiskAssessment, top: int = 3) -> str:
    parts = []
    for c in assessment.contributions[:top]:
        if abs(c.contribution) < 0.05:
            continue
        direction = "raises" if c.contribution > 0 else "lowers"
        parts.append(f"{c.label} ({_fmt(c.value, c.feature)}) {direction} the risk ({c.contribution:+.2f}) [{feature_evidence(c.feature)}].")
    return " ".join(parts) or "No single factor dominates the score."


RATIO_FEATURES = {"changed_line_coverage", "mutation_score", "refuted_claim_ratio", "entropy", "llm_risk"}


def _fmt(value: float, feature: str = "") -> str:
    if feature in RATIO_FEATURES:
        return f"{value:.0%}"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def llm_explanation(llm, assessment: RiskAssessment, decision: str, extra_evidence: dict[str, str]) -> tuple[str | None, list[GuardEvent]]:
    contributions = "\n".join(f"- {c.feature} = {_fmt(c.value, c.feature)} ({c.contribution:+.2f}): {c.label}" for c in assessment.contributions[:5])
    evidence = {feature_evidence(c.feature): c.label for c in assessment.contributions[:5]}
    evidence.update(extra_evidence)
    try:
        system, user = load_prompt("explain").render(
            decision=decision, risk=f"{assessment.probability:.2f}", contributions=contributions,
            evidence="\n".join(f"- {k}: {v}" for k, v in evidence.items()),
        )
        text, _ = llm.complete("explain", system, user, max_tokens=300)
    except Exception as exc:
        log.info("explanation LLM unavailable: %s", exc)
        return None, []
    guarded = enforce_citations(text, set(evidence))
    if guarded.kept == 0:
        return None, guarded.events
    return guarded.text, guarded.events
