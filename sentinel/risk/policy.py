"""Decision policy: PASS / CANARY / BLOCK from the calibrated risk and verified failures.

    if verified_test_failures > 0 or risk >= block:   BLOCK
    elif risk >= canary:                              CANARY
    else:                                             PASS

Thresholds are chosen on validation data so that the false-block rate (benign PRs blocked)
stays within a target, 5% by default.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PolicyDecision:
    decision: str
    reason: str
    thresholds: dict[str, float]


def decide(risk: float, verified_test_failures: int, thresholds: dict[str, float]) -> PolicyDecision:
    canary, block = thresholds.get("canary", 0.35), thresholds.get("block", 0.65)
    if verified_test_failures > 0:
        return PolicyDecision("BLOCK", f"{verified_test_failures} test(s) fail on the head revision", thresholds)
    if risk >= block:
        return PolicyDecision("BLOCK", f"calibrated risk {risk:.2f} >= block threshold {block:.2f}", thresholds)
    if risk >= canary:
        return PolicyDecision("CANARY", f"calibrated risk {risk:.2f} >= canary threshold {canary:.2f}", thresholds)
    return PolicyDecision("PASS", f"calibrated risk {risk:.2f} below canary threshold {canary:.2f}", thresholds)


def choose_thresholds(scores, labels, *, max_false_block: float = 0.05, canary_recall: float = 0.9) -> dict[str, float]:
    """Block threshold: lowest score that blocks at most ``max_false_block`` of benign PRs.
    Canary threshold: highest score that still routes ``canary_recall`` of defective PRs to
    canary or block (never above the block threshold)."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    benign, defective = np.sort(scores[labels == 0]), np.sort(scores[labels == 1])
    if len(benign) == 0 or len(defective) == 0:
        return {"canary": 0.35, "block": 0.65}
    candidates = np.unique(np.concatenate([scores, [1.0 + 1e-9]]))
    block = next(float(t) for t in candidates if np.mean(benign >= t) <= max_false_block)
    index = int(np.floor((1 - canary_recall) * len(defective)))
    canary = float(min(defective[min(index, len(defective) - 1)], block))
    return {"canary": round(canary, 4), "block": round(min(block, 1.0), 4)}
