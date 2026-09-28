import pytest

from unierp.core.domain import Enrollment, EnrollmentStatus
from unierp.core.errors import UnknownCourseError, ValidationError
from unierp.registration.credits import calculateStudentCredits, credit_load_limits, is_full_time

from factories import graded


def test_earned_credits_count_passing_grades_only(catalog):
    history = [graded("CS101", "2025-FALL", "A"), graded("MA101", "2025-FALL", "F")]
    assert calculateStudentCredits(history, catalog) == 4


def test_attempted_credits_include_failures(catalog):
    history = [graded("CS101", "2025-FALL", "A"), graded("MA101", "2025-FALL", "F")]
    assert calculateStudentCredits(history, catalog, mode="attempted") == 7


def test_withdrawn_courses_are_not_attempted(catalog):
    history = [graded("CS101", "2025-FALL", "B"), graded("HS101", "2025-FALL", "W")]
    assert calculateStudentCredits(history, catalog, mode="attempted") == 4


def test_dropped_courses_never_count(catalog):
    history = [
        Enrollment("S1", "CS101", "2026-FALL", EnrollmentStatus.DROPPED),
        Enrollment("S1", "MA101", "2026-FALL"),
    ]
    assert calculateStudentCredits(history, catalog, mode="in_progress") == 3


def test_term_filter(catalog):
    history = [graded("CS101", "2025-FALL", "A"), graded("CS102", "2026-SPRING", "B")]
    assert calculateStudentCredits(history, catalog, term="2026-SPRING") == 4


def test_unknown_mode_rejected(catalog):
    with pytest.raises(ValidationError):
        calculateStudentCredits([], catalog, mode="bogus")


def test_unknown_course_rejected(catalog):
    with pytest.raises(UnknownCourseError):
        calculateStudentCredits([graded("XX999", "2025-FALL", "A")], catalog)


def test_credit_load_limits():
    assert credit_load_limits(3.0) == (12, 20)
    assert credit_load_limits(1.8) == (12, 16)
    assert credit_load_limits(3.2, on_probation=True) == (12, 16)


def test_full_time_threshold():
    assert is_full_time(12)
    assert not is_full_time(11)
