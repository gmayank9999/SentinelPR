"""Request bodies for the HTTP API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class StudentIn(BaseModel):
    name: str
    email: str
    program: str
    admitted_on: date
    year: int = 1
    scholarship_pct: int = 0
    is_international: bool = False


class RegistrationIn(BaseModel):
    student_id: str
    course_code: str
    term: str


class DropIn(BaseModel):
    today: date
    term_start: date


class GradeIn(BaseModel):
    grade: str


class AttendanceIn(BaseModel):
    student_id: str
    course_code: str
    session_date: date
    present: bool
    excused: bool = False


class LateFeeIn(BaseModel):
    amount: Decimal = Field(ge=0)
    due_date: date
    paid_on: date


class ScoresIn(BaseModel):
    internal: float
    final: float
    attendance_eligible: bool = True
