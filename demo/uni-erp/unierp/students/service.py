"""Student records."""

from __future__ import annotations

import re
from datetime import date

from unierp.core.domain import Student, StudentStatus
from unierp.core.errors import ValidationError
from unierp.core.store import Store

PROGRAMS = ("BTECH-CSE", "BTECH-ECE", "BSC-MATH")
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_YEAR = 5

# Allowed status transitions. Graduated and withdrawn are terminal.
TRANSITIONS: dict[StudentStatus, set[StudentStatus]] = {
    StudentStatus.ACTIVE: {StudentStatus.PROBATION, StudentStatus.SUSPENDED, StudentStatus.GRADUATED, StudentStatus.WITHDRAWN},
    StudentStatus.PROBATION: {StudentStatus.ACTIVE, StudentStatus.SUSPENDED, StudentStatus.WITHDRAWN},
    StudentStatus.SUSPENDED: {StudentStatus.ACTIVE, StudentStatus.WITHDRAWN},
    StudentStatus.GRADUATED: set(),
    StudentStatus.WITHDRAWN: set(),
}


def next_student_id(store: Store, admitted_on: date) -> str:
    prefix = f"S{admitted_on.year}"
    taken = [int(sid[len(prefix):]) for sid in store.students if sid.startswith(prefix)]
    return f"{prefix}{(max(taken, default=0) + 1):03d}"


def create_student(
    store: Store,
    *,
    name: str,
    email: str,
    program: str,
    admitted_on: date,
    year: int = 1,
    scholarship_pct: int = 0,
    is_international: bool = False,
) -> Student:
    name = " ".join(name.split())
    if len(name) < 2:
        raise ValidationError("name is too short")
    if not EMAIL_PATTERN.match(email):
        raise ValidationError(f"invalid email: {email}")
    if any(s.email.lower() == email.lower() for s in store.students.values()):
        raise ValidationError(f"email already registered: {email}")
    if program not in PROGRAMS:
        raise ValidationError(f"unknown program: {program}")
    if not 1 <= year <= MAX_YEAR:
        raise ValidationError(f"year must be 1-{MAX_YEAR}")
    if not 0 <= scholarship_pct <= 100:
        raise ValidationError("scholarship must be 0-100%")

    student = Student(
        id=next_student_id(store, admitted_on),
        name=name,
        email=email.lower(),
        program=program,
        year=year,
        admitted_on=admitted_on,
        scholarship_pct=scholarship_pct,
        is_international=is_international,
    )
    return store.add_student(student)


def change_status(store: Store, student_id: str, new_status: StudentStatus) -> Student:
    student = store.get_student(student_id)
    if new_status == student.status:
        return student
    if new_status not in TRANSITIONS[student.status]:
        raise ValidationError(f"cannot move from {student.status.value} to {new_status.value}")
    student.status = new_status
    return student


def search_students(store: Store, query: str = "", program: str | None = None) -> list[Student]:
    query = query.strip().lower()
    results = [
        s
        for s in store.students.values()
        if (not query or query in s.name.lower() or query in s.id.lower())
        and (program is None or s.program == program)
    ]
    return sorted(results, key=lambda s: s.id)
