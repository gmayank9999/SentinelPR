from datetime import date

import pytest

from unierp.core.domain import Enrollment, EnrollmentStatus, StudentStatus
from unierp.core.errors import RegistrationError, ValidationError
from unierp.registration.enrollment import drop, record_grade, register

from factories import graded


def test_register_happy_path(store):
    enrollment = register(store, "S1", "CS101", "2026-FALL")
    assert enrollment.status is EnrollmentStatus.ENROLLED
    assert store.section_size("CS101", "2026-FALL") == 1


def test_register_rejects_duplicate(store):
    register(store, "S1", "CS101", "2026-FALL")
    with pytest.raises(RegistrationError, match="already enrolled"):
        register(store, "S1", "CS101", "2026-FALL")


def test_register_rejects_missing_prerequisites(store):
    with pytest.raises(RegistrationError, match="CS101"):
        register(store, "S1", "CS102", "2026-FALL")


def test_register_rejects_timetable_clash(store):
    register(store, "S1", "CS101", "2026-FALL")
    with pytest.raises(RegistrationError, match="clash"):
        register(store, "S1", "MA101", "2026-FALL")


def test_register_rejects_suspended_student(store):
    store.get_student("S1").status = StudentStatus.SUSPENDED
    with pytest.raises(RegistrationError, match="suspended"):
        register(store, "S1", "CS101", "2026-FALL")


def test_register_rejects_already_passed_course(store):
    store.add_enrollment(graded("CS101", "2025-FALL", "B"))
    with pytest.raises(RegistrationError, match="already passed"):
        register(store, "S1", "CS101", "2026-FALL")


def test_drop_inside_window(store):
    register(store, "S1", "CS101", "2026-FALL")
    e = drop(store, "S1", "CS101", "2026-FALL", today=date(2026, 8, 10), term_start=date(2026, 8, 3))
    assert e.status is EnrollmentStatus.DROPPED


def test_drop_after_window_records_w(store):
    register(store, "S1", "CS101", "2026-FALL")
    e = drop(store, "S1", "CS101", "2026-FALL", today=date(2026, 9, 30), term_start=date(2026, 8, 3))
    assert e.grade == "W"


def test_record_grade_sets_status(store):
    store.add_enrollment(Enrollment("S1", "CS101", "2026-FALL"))
    assert record_grade(store, "S1", "CS101", "2026-FALL", "f").status is EnrollmentStatus.FAILED


def test_cannot_grade_dropped(store):
    store.add_enrollment(Enrollment("S1", "CS101", "2026-FALL", EnrollmentStatus.DROPPED))
    with pytest.raises(ValidationError):
        record_grade(store, "S1", "CS101", "2026-FALL", "A")
