"""Domain entities shared by every UniERP module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class StudentStatus(str, Enum):
    ACTIVE = "active"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    GRADUATED = "graduated"
    WITHDRAWN = "withdrawn"


class EnrollmentStatus(str, Enum):
    ENROLLED = "enrolled"
    COMPLETED = "completed"
    FAILED = "failed"
    DROPPED = "dropped"


@dataclass
class Student:
    id: str
    name: str
    email: str
    program: str
    year: int
    admitted_on: date
    status: StudentStatus = StudentStatus.ACTIVE
    scholarship_pct: int = 0
    is_international: bool = False

    @property
    def can_register(self) -> bool:
        return self.status in (StudentStatus.ACTIVE, StudentStatus.PROBATION)


@dataclass(frozen=True)
class Course:
    code: str
    title: str
    credits: int
    department: str
    level: int = 100
    prerequisites: tuple[str, ...] = ()
    capacity: int = 60
    slot: str | None = None
    lab: bool = False


@dataclass
class Enrollment:
    student_id: str
    course_code: str
    term: str
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    grade: str | None = None
    enrolled_on: date | None = None

    @property
    def is_graded(self) -> bool:
        return self.status in (EnrollmentStatus.COMPLETED, EnrollmentStatus.FAILED)


@dataclass
class AttendanceRecord:
    student_id: str
    course_code: str
    session_date: date
    present: bool
    excused: bool = False


@dataclass
class Invoice:
    id: str
    student_id: str
    term: str
    issued_on: date
    due_date: date
    items: dict[str, Decimal] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.items.values(), Decimal("0"))


@dataclass
class Payment:
    invoice_id: str
    amount: Decimal
    paid_on: date
