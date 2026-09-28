"""In-memory persistence. Good enough for a demo service and trivially resettable in tests."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from unierp.core.domain import (
    AttendanceRecord,
    Course,
    Enrollment,
    EnrollmentStatus,
    Invoice,
    Payment,
    Student,
)
from unierp.core.errors import NotFound, UnknownCourseError, UnknownStudentError


class Store:
    def __init__(self) -> None:
        self.students: dict[str, Student] = {}
        self.catalog: dict[str, Course] = {}
        self.enrollments: list[Enrollment] = []
        self.attendance: list[AttendanceRecord] = []
        self.invoices: dict[str, Invoice] = {}
        self.payments: list[Payment] = []
        self.lock = threading.RLock()

    # students -------------------------------------------------------------
    def add_student(self, student: Student) -> Student:
        with self.lock:
            self.students[student.id] = student
        return student

    def get_student(self, student_id: str) -> Student:
        try:
            return self.students[student_id]
        except KeyError:
            raise UnknownStudentError(student_id) from None

    # catalog --------------------------------------------------------------
    def add_courses(self, courses: Iterable[Course]) -> None:
        with self.lock:
            for course in courses:
                self.catalog[course.code] = course

    def get_course(self, code: str) -> Course:
        try:
            return self.catalog[code]
        except KeyError:
            raise UnknownCourseError(code) from None

    # enrollments ----------------------------------------------------------
    def add_enrollment(self, enrollment: Enrollment) -> Enrollment:
        with self.lock:
            self.enrollments.append(enrollment)
        return enrollment

    def enrollments_for(self, student_id: str, term: str | None = None) -> list[Enrollment]:
        return [
            e
            for e in self.enrollments
            if e.student_id == student_id and (term is None or e.term == term)
        ]

    def find_enrollment(self, student_id: str, course_code: str, term: str) -> Enrollment:
        for e in self.enrollments:
            if (e.student_id, e.course_code, e.term) == (student_id, course_code, term):
                return e
        raise NotFound(f"{student_id} is not enrolled in {course_code} for {term}")

    def section_size(self, course_code: str, term: str) -> int:
        return sum(
            1
            for e in self.enrollments
            if e.course_code == course_code
            and e.term == term
            and e.status is EnrollmentStatus.ENROLLED
        )

    # attendance -----------------------------------------------------------
    def attendance_for(self, student_id: str, course_code: str) -> list[AttendanceRecord]:
        return [
            r
            for r in self.attendance
            if r.student_id == student_id and r.course_code == course_code
        ]

    # fees -----------------------------------------------------------------
    def get_invoice(self, invoice_id: str) -> Invoice:
        try:
            return self.invoices[invoice_id]
        except KeyError:
            raise NotFound(f"unknown invoice: {invoice_id}") from None

    def payments_for(self, invoice_id: str) -> list[Payment]:
        return [p for p in self.payments if p.invoice_id == invoice_id]
