"""Degree audit: how far a student is from graduating."""

from __future__ import annotations

from dataclasses import dataclass, field

from unierp.core.store import Store
from unierp.exam.grades import compute_gpa
from unierp.registration.credits import calculateStudentCredits
from unierp.registration.prerequisites import passed_courses

MIN_GRADUATION_CGPA = 2.0


@dataclass(frozen=True)
class ProgramRequirements:
    total_credits: int
    core_courses: tuple[str, ...]


REQUIREMENTS: dict[str, ProgramRequirements] = {
    "BTECH-CSE": ProgramRequirements(40, ("CS101", "CS102", "CS201", "CS202", "MA101")),
    "BTECH-ECE": ProgramRequirements(40, ("EC101", "EC201", "MA101", "CS101")),
    "BSC-MATH": ProgramRequirements(36, ("MA101", "MA201", "MA202")),
}


@dataclass
class AuditResult:
    student_id: str
    program: str
    earned_credits: int
    required_credits: int
    cgpa: float
    missing_core: list[str] = field(default_factory=list)

    @property
    def remaining_credits(self) -> int:
        return max(self.required_credits - self.earned_credits, 0)

    @property
    def eligible_to_graduate(self) -> bool:
        return (
            self.remaining_credits == 0
            and not self.missing_core
            and self.cgpa >= MIN_GRADUATION_CGPA
        )

    def as_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "program": self.program,
            "earned_credits": self.earned_credits,
            "required_credits": self.required_credits,
            "remaining_credits": self.remaining_credits,
            "cgpa": self.cgpa,
            "missing_core": self.missing_core,
            "eligible_to_graduate": self.eligible_to_graduate,
        }


def degree_audit(store: Store, student_id: str) -> AuditResult:
    student = store.get_student(student_id)
    requirements = REQUIREMENTS[student.program]
    history = store.enrollments_for(student_id)
    done = passed_courses(history)
    return AuditResult(
        student_id=student_id,
        program=student.program,
        earned_credits=calculateStudentCredits(history, store.catalog, mode="earned"),
        required_credits=requirements.total_credits,
        cgpa=compute_gpa(history, store.catalog),
        missing_core=[c for c in requirements.core_courses if c not in done],
    )
