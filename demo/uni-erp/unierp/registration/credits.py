"""Credit accounting.

Almost every other module leans on :func:`calculateStudentCredits`: GPA uses attempted
credits, the degree audit uses earned credits, and both the credit-load check and tuition
use in-progress credits for the current term.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from unierp.core.domain import Course, Enrollment, EnrollmentStatus
from unierp.core.errors import UnknownCourseError, ValidationError
from unierp.exam.grading_scale import counts_toward_gpa, is_passing

CREDIT_MODES = ("earned", "attempted", "in_progress")

NORMAL_MAX_CREDITS = 20
HONOURS_MAX_CREDITS = 24
PROBATION_MAX_CREDITS = 16
FULL_TIME_MIN_CREDITS = 12
HONOURS_CGPA = 3.5
PROBATION_CGPA = 2.0


def calculateStudentCredits(  # noqa: N802 - name is part of the public API
    enrollments: Iterable[Enrollment],
    catalog: Mapping[str, Course],
    *,
    term: str | None = None,
    mode: str = "earned",
) -> int:
    """Sum course credits for a student's enrollments.

    ``earned``      completed courses with a passing grade
    ``attempted``   graded courses whose grade carries grade points (the GPA denominator)
    ``in_progress`` courses the student is currently enrolled in

    Dropped enrollments never count toward any mode (see issue #87).
    """
    if mode not in CREDIT_MODES:
        raise ValidationError(f"unknown credit mode: {mode!r}")

    total = 0
    for enrollment in enrollments:
        if term is not None and enrollment.term != term:
            continue
        if enrollment.status is EnrollmentStatus.DROPPED:
            continue
        course = catalog.get(enrollment.course_code)
        if course is None:
            raise UnknownCourseError(enrollment.course_code)

        if mode == "earned":
            if enrollment.status is EnrollmentStatus.COMPLETED and is_passing(enrollment.grade):
                total += course.credits
        elif mode == "attempted":
            if enrollment.is_graded and counts_toward_gpa(enrollment.grade):
                total += course.credits
        elif enrollment.status is EnrollmentStatus.ENROLLED:
            total += course.credits
    return total


def credit_load_limits(cgpa: float, *, on_probation: bool = False) -> tuple[int, int]:
    """Return the (minimum, maximum) credits a student may carry in one term."""
    if on_probation or (0 < cgpa < PROBATION_CGPA):
        return FULL_TIME_MIN_CREDITS, PROBATION_MAX_CREDITS
    if cgpa >= HONOURS_CGPA:
        return FULL_TIME_MIN_CREDITS, HONOURS_MAX_CREDITS
    return FULL_TIME_MIN_CREDITS, NORMAL_MAX_CREDITS


def is_full_time(in_progress_credits: int) -> bool:
    return in_progress_credits >= FULL_TIME_MIN_CREDITS
