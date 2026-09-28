"""Attendance tracking and exam eligibility.

Students need 75% attendance to sit the final exam. Medically excused absences can be
condoned, but condonation is capped at 10 percentage points (Academic Council rule).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from unierp.core.domain import AttendanceRecord, EnrollmentStatus
from unierp.core.errors import ValidationError
from unierp.core.store import Store

MIN_ATTENDANCE_PCT = 75.0
MAX_CONDONATION_PCT = 10.0


def attendance_percentage(records: Iterable[AttendanceRecord]) -> float:
    records = list(records)
    if not records:
        return 100.0
    present = sum(1 for r in records if r.present)
    return round(100.0 * present / len(records), 1)


def condoned_percentage(records: Iterable[AttendanceRecord]) -> float:
    records = list(records)
    if not records:
        return 100.0
    base = attendance_percentage(records)
    excused = sum(1 for r in records if not r.present and r.excused)
    credit = min(100.0 * excused / len(records), MAX_CONDONATION_PCT)
    return round(min(base + credit, 100.0), 1)


def is_exam_eligible(records: Iterable[AttendanceRecord]) -> bool:
    return condoned_percentage(records) >= MIN_ATTENDANCE_PCT


def mark_attendance(
    store: Store,
    student_id: str,
    course_code: str,
    session_date: date,
    *,
    present: bool,
    excused: bool = False,
) -> AttendanceRecord:
    store.get_student(student_id)
    store.get_course(course_code)
    if present and excused:
        raise ValidationError("a present student cannot also be excused")
    for record in store.attendance_for(student_id, course_code):
        if record.session_date == session_date:
            record.present, record.excused = present, excused
            return record
    record = AttendanceRecord(student_id, course_code, session_date, present, excused)
    with store.lock:
        store.attendance.append(record)
    return record


@dataclass
class ShortageEntry:
    student_id: str
    percentage: float
    condoned: float


def shortage_report(store: Store, course_code: str, term: str) -> list[ShortageEntry]:
    """Enrolled students who are currently below the eligibility threshold."""
    report = []
    for e in store.enrollments:
        if e.course_code != course_code or e.term != term or e.status is not EnrollmentStatus.ENROLLED:
            continue
        records = store.attendance_for(e.student_id, course_code)
        condoned = condoned_percentage(records)
        if condoned < MIN_ATTENDANCE_PCT:
            report.append(ShortageEntry(e.student_id, attendance_percentage(records), condoned))
    return sorted(report, key=lambda entry: entry.condoned)
