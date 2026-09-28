"""Demo dataset used by the API server and the load generator."""

from __future__ import annotations

from datetime import date, timedelta

from unierp.core.domain import AttendanceRecord, Course, Enrollment, EnrollmentStatus, Student
from unierp.core.store import Store

COURSES = [
    Course("CS101", "Programming Fundamentals", 4, "CSE", 100, (), 80, "A", lab=True),
    Course("CS102", "Data Structures", 4, "CSE", 100, ("CS101",), 60, "B", lab=True),
    Course("CS201", "Algorithms", 4, "CSE", 200, ("CS102", "MA101"), 60, "C"),
    Course("CS202", "Databases", 3, "CSE", 200, ("CS102",), 60, "D"),
    Course("CS301", "Operating Systems", 4, "CSE", 300, ("CS201",), 50, "A"),
    Course("CS302", "Machine Learning", 4, "CSE", 300, ("CS201", "MA201"), 40, "E"),
    Course("MA101", "Calculus", 4, "MATH", 100, (), 120, "E"),
    Course("MA201", "Linear Algebra", 3, "MATH", 200, ("MA101",), 90, "F"),
    Course("MA202", "Probability", 3, "MATH", 200, ("MA101",), 90, "G"),
    Course("EC101", "Circuits", 4, "ECE", 100, (), 60, "C", lab=True),
    Course("EC201", "Signals and Systems", 4, "ECE", 200, ("EC101", "MA101"), 60, "D"),
    Course("HS101", "Technical Writing", 2, "HSS", 100, (), 100, "H"),
]

STUDENTS = [
    Student("S2024001", "Aarav Mehta", "aarav.mehta@uni.edu", "BTECH-CSE", 3, date(2024, 7, 20)),
    Student("S2024002", "Diya Nair", "diya.nair@uni.edu", "BTECH-CSE", 3, date(2024, 7, 20), scholarship_pct=50),
    Student("S2024003", "Kabir Singh", "kabir.singh@uni.edu", "BTECH-ECE", 3, date(2024, 7, 21)),
    Student("S2024004", "Meera Iyer", "meera.iyer@uni.edu", "BSC-MATH", 3, date(2024, 7, 22)),
    Student("S2025001", "Rohan Das", "rohan.das@uni.edu", "BTECH-CSE", 2, date(2025, 7, 19), is_international=True),
    Student("S2025002", "Ananya Rao", "ananya.rao@uni.edu", "BTECH-CSE", 2, date(2025, 7, 19)),
    Student("S2025003", "Ishaan Kapoor", "ishaan.kapoor@uni.edu", "BTECH-ECE", 2, date(2025, 7, 20)),
    Student("S2026001", "Sara Khan", "sara.khan@uni.edu", "BSC-MATH", 1, date(2026, 7, 18)),
]

_GRADED = [
    # (student, course, term, grade)
    ("S2024001", "CS101", "2024-FALL", "A"),
    ("S2024001", "MA101", "2024-FALL", "B+"),
    ("S2024001", "HS101", "2024-FALL", "A-"),
    ("S2024001", "CS102", "2025-SPRING", "A-"),
    ("S2024001", "MA201", "2025-SPRING", "B"),
    ("S2024001", "CS201", "2025-FALL", "B+"),
    ("S2024001", "CS202", "2025-FALL", "A"),
    ("S2024002", "CS101", "2024-FALL", "B"),
    ("S2024002", "MA101", "2024-FALL", "C+"),
    ("S2024002", "CS102", "2025-SPRING", "F"),
    ("S2024002", "CS102", "2025-FALL", "B-"),
    ("S2024002", "MA202", "2025-FALL", "C"),
    ("S2024003", "EC101", "2024-FALL", "A"),
    ("S2024003", "MA101", "2024-FALL", "A-"),
    ("S2024003", "EC201", "2025-SPRING", "B+"),
    ("S2024003", "CS101", "2025-FALL", "W"),
    ("S2024004", "MA101", "2024-FALL", "A"),
    ("S2024004", "MA201", "2025-SPRING", "A"),
    ("S2024004", "MA202", "2025-SPRING", "A-"),
    ("S2025001", "CS101", "2025-FALL", "C"),
    ("S2025001", "MA101", "2025-FALL", "D"),
    ("S2025002", "CS101", "2025-FALL", "A"),
    ("S2025002", "MA101", "2025-FALL", "A"),
    ("S2025003", "EC101", "2025-FALL", "B"),
]

CURRENT_TERM = "2026-FALL"
_CURRENT = [
    ("S2024001", "CS301"),
    ("S2024001", "CS302"),
    ("S2024002", "CS201"),
    ("S2024003", "CS101"),
    ("S2024004", "CS101"),
    ("S2025001", "CS102"),
    ("S2025002", "CS102"),
    ("S2025002", "MA201"),
    ("S2025003", "EC201"),
    ("S2026001", "MA101"),
]


def build_demo_store() -> Store:
    store = Store()
    store.add_courses(COURSES)
    for student in STUDENTS:
        store.add_student(Student(**student.__dict__))
    for sid, code, term, grade in _GRADED:
        status = EnrollmentStatus.FAILED if grade == "F" else EnrollmentStatus.COMPLETED
        store.add_enrollment(Enrollment(sid, code, term, status, grade))
    for sid, code in _CURRENT:
        store.add_enrollment(Enrollment(sid, code, CURRENT_TERM))

    start = date(2026, 8, 3)
    for index, (sid, code) in enumerate(_CURRENT):
        for day in range(12):
            present = (day + index) % (4 + index % 3) != 0
            store.attendance.append(
                AttendanceRecord(sid, code, start + timedelta(days=2 * day), present, excused=not present and day % 2 == 0)
            )
    return store
