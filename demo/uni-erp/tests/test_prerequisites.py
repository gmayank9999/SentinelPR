import pytest

from unierp.core.domain import Course, Enrollment
from unierp.core.errors import PrerequisiteCycleError
from unierp.registration.prerequisites import (
    check_prerequisites,
    find_cycles,
    prerequisite_chain,
    unlocked_courses,
)

from factories import graded


def test_prerequisites_satisfied(catalog):
    history = [graded("CS101", "2025-FALL", "B")]
    assert check_prerequisites(history, catalog["CS102"]).satisfied


def test_failed_prerequisite_is_missing(catalog):
    result = check_prerequisites([graded("CS101", "2025-FALL", "F")], catalog["CS102"])
    assert not result.satisfied
    assert result.missing == ["CS101"]


def test_in_progress_prerequisite_does_not_count(catalog):
    history = [Enrollment("S1", "CS101", "2026-FALL")]
    assert not check_prerequisites(history, catalog["CS102"]).satisfied


def test_reports_every_missing_prerequisite(catalog):
    result = check_prerequisites([], catalog["CS201"])
    assert result.missing == ["CS102", "MA101"]


def test_prerequisite_chain_orders_deepest_first(catalog):
    assert prerequisite_chain("CS201", catalog) == ["CS101", "CS102", "MA101"]


def test_cycle_detection():
    cyclic = {
        "A": Course("A", "A", 3, "X", prerequisites=("B",)),
        "B": Course("B", "B", 3, "X", prerequisites=("A",)),
    }
    with pytest.raises(PrerequisiteCycleError):
        prerequisite_chain("A", cyclic)
    assert len(find_cycles(cyclic)) == 1


def test_unlocked_courses(catalog):
    history = [graded("CS101", "2025-FALL", "A")]
    assert unlocked_courses(history, catalog) == ["CS102", "HS101", "MA101"]
