from datetime import date
from decimal import Decimal

import pytest

from unierp.core.domain import Enrollment, EnrollmentStatus, Student
from unierp.core.errors import ValidationError
from unierp.fees.calculator import (
    apply_late_fee,
    apply_scholarship,
    late_fee_rate,
    round_fee,
    term_fee_breakdown,
    tuition_for_term,
)

DUE = date(2026, 9, 1)


def student(**overrides):
    base = dict(id="S1", name="A", email="a@uni.edu", program="BTECH-CSE", year=1, admitted_on=date(2026, 7, 1))
    base.update(overrides)
    return Student(**base)


def test_round_fee_rounds_down():
    assert round_fee(Decimal("1999.99")) == Decimal("1999")


def test_no_late_fee_within_grace_period():
    assert apply_late_fee(Decimal("10000"), DUE, date(2026, 9, 4)) == Decimal("10000")


def test_late_fee_per_started_week():
    assert late_fee_rate(DUE, date(2026, 9, 5)) == Decimal("0.02")
    assert apply_late_fee(Decimal("10000"), DUE, date(2026, 9, 9)) == Decimal("10400")


def test_late_fee_is_capped():
    assert apply_late_fee(Decimal("10000"), DUE, date(2026, 12, 31)) == Decimal("11000")


def test_late_fee_result_is_rounded_down():
    assert apply_late_fee(Decimal("999.99"), DUE, date(2026, 9, 5)) == Decimal("1019")


def test_negative_amount_rejected():
    with pytest.raises(ValidationError):
        apply_late_fee(Decimal("-1"), DUE, DUE)


def test_tuition_uses_in_progress_credits(catalog):
    enrollments = [
        Enrollment("S1", "CS101", "2026-FALL"),
        Enrollment("S1", "MA101", "2026-FALL", EnrollmentStatus.DROPPED),
    ]
    assert tuition_for_term(enrollments, catalog, "2026-FALL", student()) == Decimal("10000")


def test_international_surcharge(catalog):
    enrollments = [Enrollment("S1", "CS101", "2026-FALL")]
    assert tuition_for_term(enrollments, catalog, "2026-FALL", student(is_international=True)) == Decimal("12500")


def test_scholarship_bounds():
    assert apply_scholarship(Decimal("10000"), 25) == Decimal("7500")
    with pytest.raises(ValidationError):
        apply_scholarship(Decimal("10000"), 120)


def test_breakdown_adds_lab_fee_and_applies_scholarship(catalog):
    enrollments = [Enrollment("S1", "CS101", "2026-FALL"), Enrollment("S1", "HS101", "2026-FALL")]
    items = term_fee_breakdown(student(scholarship_pct=50), enrollments, catalog, "2026-FALL")
    assert items["tuition"] == Decimal("7500")
    assert items["lab"] == Decimal("2000")
    assert set(items) == {"tuition", "registration", "library", "lab"}
