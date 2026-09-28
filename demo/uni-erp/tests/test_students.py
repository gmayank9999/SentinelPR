from datetime import date

import pytest

from unierp.core.domain import StudentStatus
from unierp.core.errors import UnknownStudentError, ValidationError
from unierp.students.service import change_status, create_student, search_students


def new(store, **overrides):
    payload = dict(name="Nisha  Verma", email="Nisha@uni.edu", program="BTECH-CSE", admitted_on=date(2026, 7, 15))
    payload.update(overrides)
    return create_student(store, **payload)


def test_create_student_normalises_fields(store):
    s = new(store)
    assert s.id == "S2026001"
    assert s.name == "Nisha Verma"
    assert s.email == "nisha@uni.edu"


def test_ids_are_sequential_per_year(store):
    new(store)
    assert new(store, email="other@uni.edu").id == "S2026002"


@pytest.mark.parametrize(
    "field, value",
    [("email", "not-an-email"), ("program", "MBA"), ("year", 7), ("name", "X"), ("scholarship_pct", 101)],
)
def test_create_student_validation(store, field, value):
    with pytest.raises(ValidationError):
        new(store, **{field: value})


def test_duplicate_email_rejected(store):
    new(store)
    with pytest.raises(ValidationError, match="already registered"):
        new(store, email="NISHA@uni.edu")


def test_status_transitions(store):
    change_status(store, "S1", StudentStatus.PROBATION)
    change_status(store, "S1", StudentStatus.ACTIVE)
    change_status(store, "S1", StudentStatus.GRADUATED)
    with pytest.raises(ValidationError):
        change_status(store, "S1", StudentStatus.ACTIVE)


def test_unknown_student(store):
    with pytest.raises(UnknownStudentError):
        change_status(store, "NOPE", StudentStatus.ACTIVE)


def test_search(demo_store):
    assert [s.id for s in search_students(demo_store, "rao")] == ["S2025002"]
    assert len(search_students(demo_store, program="BSC-MATH")) == 2
