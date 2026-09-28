"""Official transcript."""

from __future__ import annotations

from typing import Any

from unierp.core.store import Store
from unierp.core.terms import sort_terms
from unierp.exam.grades import compute_gpa
from unierp.registration.credits import calculateStudentCredits


def build_transcript(store: Store, student_id: str) -> dict[str, Any]:
    student = store.get_student(student_id)
    history = [e for e in store.enrollments_for(student_id) if e.is_graded]

    terms = []
    for term in sort_terms(e.term for e in history):
        rows = [
            {
                "code": e.course_code,
                "title": store.catalog[e.course_code].title,
                "credits": store.catalog[e.course_code].credits,
                "grade": e.grade,
            }
            for e in sorted(history, key=lambda e: e.course_code)
            if e.term == term
        ]
        terms.append(
            {
                "term": term,
                "courses": rows,
                "term_gpa": compute_gpa(history, store.catalog, term=term),
                "earned_credits": calculateStudentCredits(history, store.catalog, term=term, mode="earned"),
            }
        )

    return {
        "student": {"id": student.id, "name": student.name, "program": student.program},
        "terms": terms,
        "cgpa": compute_gpa(history, store.catalog),
        "earned_credits": calculateStudentCredits(history, store.catalog, mode="earned"),
        "attempted_credits": calculateStudentCredits(history, store.catalog, mode="attempted"),
    }
