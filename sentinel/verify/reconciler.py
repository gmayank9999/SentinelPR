"""Reconciler: turn per-claim verdicts into a verified picture of the change."""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinel.models import Claim, Verdict, VerdictStatus

RETRY_REFUTED_RATIO = 0.5


@dataclass
class Reconciliation:
    verdicts: dict[str, Verdict]
    counts: dict[str, int] = field(default_factory=dict)
    impact_refuted_ratio: float = 0.0
    should_retry_impact: bool = False
    feedback: str = ""

    def status(self, claim_id: str) -> VerdictStatus | None:
        verdict = self.verdicts.get(claim_id)
        return verdict.status if verdict else None


def reconcile(claims: list[Claim], verdicts: list[Verdict], retried: bool = False) -> Reconciliation:
    by_id = {v.claim_id: v for v in verdicts}
    counts = {s.value: 0 for s in VerdictStatus}
    for v in verdicts:
        counts[v.status.value] += 1

    impact = [c for c in claims if c.agent == "impact" and c.claim_id in by_id]
    checked = [c for c in impact if by_id[c.claim_id].status is not VerdictStatus.UNVERIFIABLE]
    refuted = [c for c in checked if by_id[c.claim_id].status is VerdictStatus.REFUTED]
    ratio = len(refuted) / len(checked) if checked else 0.0
    feedback = "\n".join(f"- {c.type} {c.target}: {by_id[c.claim_id].detail}" for c in refuted[:15])
    return Reconciliation(
        verdicts=by_id,
        counts=counts,
        impact_refuted_ratio=round(ratio, 3),
        should_retry_impact=not retried and ratio > RETRY_REFUTED_RATIO and len(checked) >= 2,
        feedback=feedback,
    )
