"""Fee calculation.

All amounts are in rupees. Totals are rounded *down* to the whole rupee: the finance
office does not bill paise and rounding in the student's favour avoids disputes over
fractional amounts (issue #42).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_DOWN, Decimal

from unierp.core.domain import Course, Enrollment, EnrollmentStatus, Student
from unierp.core.errors import ValidationError
from unierp.registration.credits import calculateStudentCredits

TUITION_PER_CREDIT = Decimal("2500")
REGISTRATION_FEE = Decimal("1500")
LIBRARY_FEE = Decimal("800")
LAB_FEE = Decimal("2000")
INTERNATIONAL_SURCHARGE = Decimal("0.25")

LATE_FEE_RATE_PER_WEEK = Decimal("0.02")
LATE_FEE_CAP = Decimal("0.10")
GRACE_DAYS = 3


def round_fee(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("1"), rounding=ROUND_DOWN)


def tuition_for_term(
    enrollments: Iterable[Enrollment],
    catalog: Mapping[str, Course],
    term: str,
    student: Student,
) -> Decimal:
    credits = calculateStudentCredits(enrollments, catalog, term=term, mode="in_progress")
    tuition = TUITION_PER_CREDIT * credits
    if student.is_international:
        tuition += tuition * INTERNATIONAL_SURCHARGE
    return tuition


def apply_scholarship(tuition: Decimal, scholarship_pct: int) -> Decimal:
    if not 0 <= scholarship_pct <= 100:
        raise ValidationError(f"scholarship must be 0-100%, got {scholarship_pct}")
    return tuition * (Decimal(100 - scholarship_pct) / Decimal(100))


def term_fee_breakdown(
    student: Student,
    enrollments: Iterable[Enrollment],
    catalog: Mapping[str, Course],
    term: str,
) -> dict[str, Decimal]:
    """Itemised fees for a term. Scholarships apply to tuition only."""
    current = [
        e for e in enrollments if e.term == term and e.status is EnrollmentStatus.ENROLLED
    ]
    tuition = apply_scholarship(tuition_for_term(current, catalog, term, student), student.scholarship_pct)
    items = {
        "tuition": round_fee(tuition),
        "registration": REGISTRATION_FEE,
        "library": LIBRARY_FEE,
    }
    if any(catalog[e.course_code].lab for e in current):
        items["lab"] = LAB_FEE
    return items


def late_fee_rate(due_date: date, paid_on: date) -> Decimal:
    days_late = (paid_on - due_date).days
    if days_late <= GRACE_DAYS:
        return Decimal("0")
    weeks = math.ceil(days_late / 7)
    return min(LATE_FEE_RATE_PER_WEEK * weeks, LATE_FEE_CAP)


def apply_late_fee(amount: Decimal, due_date: date, paid_on: date) -> Decimal:
    """Amount payable including the late fee: 2% per started week late, capped at 10%.

    Payments within the three-day grace period are not charged.
    """
    if amount < 0:
        raise ValidationError("amount cannot be negative")
    rate = late_fee_rate(due_date, paid_on)
    return round_fee(amount * (Decimal("1") + rate))
