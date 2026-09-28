"""Course registration: add, drop and grade recording."""

from __future__ import annotations

from datetime import date, timedelta

from unierp.core.domain import Enrollment, EnrollmentStatus, StudentStatus
from unierp.core.errors import RegistrationError, ValidationError
from unierp.core.store import Store
from unierp.core.terms import parse_term
from unierp.exam.grades import compute_gpa
from unierp.exam.grading_scale import is_passing, validate_grade
from unierp.registration.credits import calculateStudentCredits, credit_load_limits
from unierp.registration.prerequisites import check_prerequisites

ADD_DROP_WINDOW = timedelta(days=14)


def register(store: Store, student_id: str, course_code: str, term: str) -> Enrollment:
    parse_term(term)
    student = store.get_student(student_id)
    course = store.get_course(course_code)

    if not student.can_register:
        raise RegistrationError(f"student {student_id} is {student.status.value}")

    history = store.enrollments_for(student_id)
    for e in history:
        if e.course_code != course_code:
            continue
        if e.term == term and e.status is EnrollmentStatus.ENROLLED:
            raise RegistrationError(f"already enrolled in {course_code} for {term}")
        if e.status is EnrollmentStatus.COMPLETED and is_passing(e.grade) and e.grade != "D":
            raise RegistrationError(f"{course_code} already passed; only D grades may be repeated")

    if store.section_size(course_code, term) >= course.capacity:
        raise RegistrationError(f"{course_code} is full for {term}")

    prereqs = check_prerequisites(history, course)
    if not prereqs.satisfied:
        raise RegistrationError(f"missing prerequisites: {', '.join(prereqs.missing)}")

    current = [e for e in history if e.term == term and e.status is EnrollmentStatus.ENROLLED]
    if course.slot is not None:
        for e in current:
            other = store.get_course(e.course_code)
            if other.slot == course.slot:
                raise RegistrationError(f"timetable clash with {other.code} in slot {course.slot}")

    load = calculateStudentCredits(current, store.catalog, term=term, mode="in_progress")
    cgpa = compute_gpa(history, store.catalog)
    _, max_credits = credit_load_limits(cgpa, on_probation=student.status is StudentStatus.PROBATION)
    if load + course.credits > max_credits:
        raise RegistrationError(
            f"credit limit exceeded: {load} + {course.credits} > {max_credits}"
        )

    return store.add_enrollment(
        Enrollment(student_id=student_id, course_code=course_code, term=term, enrolled_on=date.today())
    )


def drop(
    store: Store,
    student_id: str,
    course_code: str,
    term: str,
    *,
    today: date,
    term_start: date,
) -> Enrollment:
    """Drop inside the add/drop window; afterwards the course is recorded as a W."""
    enrollment = store.find_enrollment(student_id, course_code, term)
    if enrollment.status is not EnrollmentStatus.ENROLLED:
        raise RegistrationError(f"cannot drop a {enrollment.status.value} enrollment")
    if today <= term_start + ADD_DROP_WINDOW:
        enrollment.status = EnrollmentStatus.DROPPED
    else:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.grade = "W"
    return enrollment


def record_grade(store: Store, student_id: str, course_code: str, term: str, grade: str) -> Enrollment:
    enrollment = store.find_enrollment(student_id, course_code, term)
    if enrollment.status is EnrollmentStatus.DROPPED:
        raise ValidationError("cannot grade a dropped enrollment")
    grade = validate_grade(grade)
    enrollment.grade = grade
    enrollment.status = EnrollmentStatus.FAILED if grade == "F" else EnrollmentStatus.COMPLETED
    return enrollment
