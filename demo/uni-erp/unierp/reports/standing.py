"""Academic standing and honours."""

from __future__ import annotations

from enum import Enum

from unierp.core.store import Store
from unierp.exam.grades import compute_gpa
from unierp.registration.credits import FULL_TIME_MIN_CREDITS, calculateStudentCredits

PROBATION_THRESHOLD = 2.0
DISMISSAL_THRESHOLD = 1.5
DEANS_LIST_GPA = 3.7
# Standing is only assessed once a student has a meaningful record.
MIN_CREDITS_FOR_STANDING = 8


class Standing(str, Enum):
    GOOD = "good"
    PROBATION = "probation"
    DISMISSAL = "dismissal"


def academic_standing(cgpa: float, attempted_credits: int, *, previously_on_probation: bool = False) -> Standing:
    if attempted_credits < MIN_CREDITS_FOR_STANDING:
        return Standing.GOOD
    if cgpa >= PROBATION_THRESHOLD:
        return Standing.GOOD
    if previously_on_probation and cgpa < DISMISSAL_THRESHOLD:
        return Standing.DISMISSAL
    return Standing.PROBATION


def on_deans_list(term_gpa: float, term_credits: int) -> bool:
    return term_gpa >= DEANS_LIST_GPA and term_credits >= FULL_TIME_MIN_CREDITS


def standing_for_student(store: Store, student_id: str) -> dict:
    student = store.get_student(student_id)
    history = store.enrollments_for(student_id)
    cgpa = compute_gpa(history, store.catalog)
    attempted = calculateStudentCredits(history, store.catalog, mode="attempted")
    standing = academic_standing(
        cgpa, attempted, previously_on_probation=student.status.value == "probation"
    )
    return {"student_id": student_id, "cgpa": cgpa, "attempted_credits": attempted, "standing": standing.value}


def deans_list(store: Store, term: str) -> list[str]:
    honours = []
    for student_id in sorted(store.students):
        history = store.enrollments_for(student_id, term)
        credits = calculateStudentCredits(history, store.catalog, term=term, mode="attempted")
        if credits and on_deans_list(compute_gpa(history, store.catalog, term=term), credits):
            honours.append(student_id)
    return honours
