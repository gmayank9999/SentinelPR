"""The university's letter-grade scale (4.0 points)."""

from __future__ import annotations

from unierp.core.errors import ValidationError

GRADE_POINTS: dict[str, float] = {
    "A": 4.0,
    "A-": 3.7,
    "B+": 3.3,
    "B": 3.0,
    "B-": 2.7,
    "C+": 2.3,
    "C": 2.0,
    "C-": 1.7,
    "D": 1.0,
    "F": 0.0,
}

# W = withdrawn after the add/drop deadline, I = incomplete, P = pass (pass/fail courses).
NON_GPA_GRADES = frozenset({"W", "I", "P"})
PASSING_GRADES = frozenset(set(GRADE_POINTS) - {"F"}) | {"P"}
ALL_GRADES = frozenset(GRADE_POINTS) | NON_GPA_GRADES

# (minimum score, letter), highest first. The minimum is inclusive.
SCORE_CUTOFFS: tuple[tuple[float, str], ...] = (
    (93.0, "A"),
    (90.0, "A-"),
    (87.0, "B+"),
    (83.0, "B"),
    (80.0, "B-"),
    (77.0, "C+"),
    (73.0, "C"),
    (70.0, "C-"),
    (60.0, "D"),
)

PASS_MARK = 60.0


def letter_for_score(score: float) -> str:
    if not 0.0 <= score <= 100.0:
        raise ValidationError(f"score must be between 0 and 100, got {score}")
    for cutoff, letter in SCORE_CUTOFFS:
        if score >= cutoff:
            return letter
    return "F"


def validate_grade(grade: str) -> str:
    normalised = grade.strip().upper()
    if normalised not in ALL_GRADES:
        raise ValidationError(f"unknown grade: {grade!r}")
    return normalised


def is_passing(grade: str | None) -> bool:
    return grade is not None and grade in PASSING_GRADES


def counts_toward_gpa(grade: str | None) -> bool:
    return grade is not None and grade in GRADE_POINTS


def grade_points(grade: str) -> float:
    try:
        return GRADE_POINTS[grade]
    except KeyError:
        raise ValidationError(f"grade {grade!r} carries no grade points") from None
