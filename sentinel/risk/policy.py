"""Decision policy: 9-category decisions from calibrated risk and verified failures.

Each base tier (PASS / CANARY / BLOCK) is subdivided into LOW / MEDIUM / HIGH based on the
position of the risk score within that tier's range:

    PASS   range: [0,      canary)  → PASS_LOW / PASS_MEDIUM / PASS_HIGH
    CANARY range: [canary, block)   → CANARY_LOW / CANARY_MEDIUM / CANARY_HIGH
    BLOCK  range: [block,  1.0]     → BLOCK_LOW / BLOCK_MEDIUM / BLOCK_HIGH

If verified_test_failures > 0 the result is always BLOCK_HIGH regardless of score.

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


def _sub_band(value: float, low: float, high: float) -> str:
    """Return 'LOW', 'MEDIUM', or 'HIGH' for *value* within [low, high].

    The range [low, high] is split into equal thirds.  Values that fall exactly
    on a boundary are assigned to the upper band so that the boundary itself
    never sits in a no-man's land.
    """
    span = high - low
    if span <= 0:
        return "LOW"
    third = span / 3.0
    if value < low + third:
        return "LOW"
    if value < low + 2 * third:
        return "MEDIUM"
    return "HIGH"


def base_decision(decision_9: str) -> str:
    """Strip the sub-band suffix from a 9-category decision.

    Examples::

        base_decision("PASS_LOW")    -> "PASS"
        base_decision("CANARY_HIGH") -> "CANARY"
        base_decision("BLOCK")       -> "BLOCK"   # already a base decision
    """
    return decision_9.split("_")[0]


def decide(risk: float, verified_test_failures: int, thresholds: dict[str, float]) -> PolicyDecision:
    """Return a fine-grained 9-category :class:`PolicyDecision`.

    Parameters
    ----------
    risk:
        Calibrated risk probability in [0.0, 1.0].
    verified_test_failures:
        Number of tests that fail on the PR head revision (pre-verified).
    thresholds:
        Dict with keys ``"canary"`` and ``"block"``.

    Returns
    -------
    PolicyDecision
        ``decision`` is one of the 12 literals (3 legacy + 9 fine-grained).
        ``reason`` embeds the computed sub-band boundaries for reporting.
    """
    canary = thresholds.get("canary", 0.35)
    block = thresholds.get("block", 0.65)

    # Hard override: any verified test failure is the most severe block.
    if verified_test_failures > 0:
        reason = (
            f"{verified_test_failures} test(s) fail on the head revision — "
            f"sub-band boundaries: pass [0, {canary:.4f}), "
            f"canary [{canary:.4f}, {block:.4f}), block [{block:.4f}, 1.0]"
        )
        return PolicyDecision("BLOCK_HIGH", reason, thresholds)

    if risk >= block:
        band = _sub_band(risk, block, 1.0)
        # Sub-band boundaries within the BLOCK tier
        third = (1.0 - block) / 3.0
        b_low = round(block, 4)
        b_med = round(block + third, 4)
        b_high = round(block + 2 * third, 4)
        reason = (
            f"risk {risk:.4f} >= block threshold {block:.4f} → BLOCK_{band} "
            f"(block sub-bands: LOW [{b_low}, {b_med}), MEDIUM [{b_med}, {b_high}), HIGH [{b_high}, 1.0])"
        )
        return PolicyDecision(f"BLOCK_{band}", reason, thresholds)

    if risk >= canary:
        band = _sub_band(risk, canary, block)
        third = (block - canary) / 3.0
        c_low = round(canary, 4)
        c_med = round(canary + third, 4)
        c_high = round(canary + 2 * third, 4)
        reason = (
            f"risk {risk:.4f} >= canary threshold {canary:.4f} → CANARY_{band} "
            f"(canary sub-bands: LOW [{c_low}, {c_med}), MEDIUM [{c_med}, {c_high}), HIGH [{c_high}, {block:.4f}))"
        )
        return PolicyDecision(f"CANARY_{band}", reason, thresholds)

    # PASS tier
    band = _sub_band(risk, 0.0, canary)
    third = canary / 3.0
    p_low = 0.0
    p_med = round(third, 4)
    p_high = round(2 * third, 4)
    reason = (
        f"risk {risk:.4f} below canary threshold {canary:.4f} → PASS_{band} "
        f"(pass sub-bands: LOW [{p_low}, {p_med:.4f}), MEDIUM [{p_med:.4f}, {p_high:.4f}), HIGH [{p_high:.4f}, {canary:.4f}))"
    )
    return PolicyDecision(f"PASS_{band}", reason, thresholds)


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
