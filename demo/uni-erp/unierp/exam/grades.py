"""GPA computation.

GPAs are computed with ``Decimal`` and rounded half-up to two places, matching what
the registrar prints on transcripts (issue #31).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal

from unierp.core.domain import Course, Enrollment
from unierp.core.errors import UnknownCourseError
from unierp.core.terms import sort_terms, term_sort_key
from unierp.exam.grading_scale import counts_toward_gpa, grade_points
from unierp.registration.credits import calculateStudentCredits

TWO_PLACES = Decimal("0.01")


def effective_attempts(enrollments: Iterable[Enrollment]) -> list[Enrollment]:
    """Apply the repeat policy: when a course is retaken, only the latest graded attempt counts."""
    latest: dict[str, Enrollment] = {}
    ungraded: list[Enrollment] = []
    for e in enrollments:
        if not e.is_graded:
            ungraded.append(e)
            continue
        current = latest.get(e.course_code)
        if current is None or term_sort_key(e.term) > term_sort_key(current.term):
            latest[e.course_code] = e
    return list(latest.values()) + ungraded


def quality_points(
    enrollments: Iterable[Enrollment],
    catalog: Mapping[str, Course],
    *,
    term: str | None = None,
) -> Decimal:
    total = Decimal("0")
    for e in enrollments:
        if term is not None and e.term != term:
            continue
        if not e.is_graded or not counts_toward_gpa(e.grade):
            continue
        course = catalog.get(e.course_code)
        if course is None:
            raise UnknownCourseError(e.course_code)
        total += Decimal(str(grade_points(e.grade))) * course.credits
    return total


def compute_gpa(
    enrollments: Iterable[Enrollment],
    catalog: Mapping[str, Course],
    *,
    term: str | None = None,
) -> float:
    """Credit-weighted GPA. Cumulative when ``term`` is None, otherwise for that term only."""
    history = list(enrollments)
    if term is None:
        history = effective_attempts(history)
    attempted = calculateStudentCredits(history, catalog, term=term, mode="attempted")
    if attempted == 0:
        return 0.0
    points = quality_points(history, catalog, term=term)
    return float((points / Decimal(attempted)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def term_gpas(enrollments: Iterable[Enrollment], catalog: Mapping[str, Course]) -> dict[str, float]:
    history = list(enrollments)
    terms = sort_terms(e.term for e in history if e.is_graded)
    return {t: compute_gpa(history, catalog, term=t) for t in terms}


def gpa_trend(enrollments: Iterable[Enrollment], catalog: Mapping[str, Course]) -> str:
    """'improving', 'declining' or 'steady' based on the last two graded terms."""
    values = list(term_gpas(enrollments, catalog).values())
    if len(values) < 2:
        return "steady"
    delta = values[-1] - values[-2]
    if delta >= 0.2:
        return "improving"
    if delta <= -0.2:
        return "declining"
    return "steady"
