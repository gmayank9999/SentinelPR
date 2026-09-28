"""Turning component marks into a final letter grade."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from unierp.core.errors import ValidationError
from unierp.exam.grading_scale import PASS_MARK, letter_for_score

DEFAULT_WEIGHTS: dict[str, float] = {"internal": 0.4, "final": 0.6}
MAX_GRACE_MARKS = 2.0


def weighted_score(components: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    weights = dict(weights or DEFAULT_WEIGHTS)
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValidationError("component weights must sum to 1")
    missing = set(weights) - set(components)
    if missing:
        raise ValidationError(f"missing components: {', '.join(sorted(missing))}")
    total = 0.0
    for name, weight in weights.items():
        mark = components[name]
        if not 0.0 <= mark <= 100.0:
            raise ValidationError(f"{name} mark out of range: {mark}")
        total += mark * weight
    return round(total, 2)


def apply_grace_marks(score: float, max_grace: float = MAX_GRACE_MARKS) -> float:
    """Lift a score that falls just short of the pass mark. Grace never changes any other band."""
    if PASS_MARK - max_grace <= score < PASS_MARK:
        return PASS_MARK
    return score


def final_grade(
    components: Mapping[str, float],
    *,
    attendance_eligible: bool = True,
    weights: Mapping[str, float] | None = None,
) -> str:
    """Letter grade for a student. Students debarred for attendance receive an F."""
    if not attendance_eligible:
        return "F"
    return letter_for_score(apply_grace_marks(weighted_score(components, weights)))


@dataclass
class ResultSummary:
    count: int
    mean: float
    median: float
    pass_rate: float
    highest: float
    lowest: float


def summarise_results(scores: Sequence[float]) -> ResultSummary:
    if not scores:
        raise ValidationError("no scores to summarise")
    passed = sum(1 for s in scores if s >= PASS_MARK)
    return ResultSummary(
        count=len(scores),
        mean=round(statistics.fmean(scores), 2),
        median=round(statistics.median(scores), 2),
        pass_rate=round(100.0 * passed / len(scores), 1),
        highest=max(scores),
        lowest=min(scores),
    )
