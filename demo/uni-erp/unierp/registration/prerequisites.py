"""Prerequisite checks and prerequisite-graph utilities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from unierp.core.domain import Course, Enrollment, EnrollmentStatus
from unierp.core.errors import PrerequisiteCycleError, UnknownCourseError
from unierp.exam.grading_scale import is_passing


@dataclass
class PrerequisiteResult:
    satisfied: bool
    missing: list[str] = field(default_factory=list)


def passed_courses(enrollments: Iterable[Enrollment]) -> set[str]:
    """Courses the student has completed with a passing grade.

    In-progress courses do not count: a prerequisite must be finished before the
    dependent course starts (issue #23).
    """
    return {
        e.course_code
        for e in enrollments
        if e.status is EnrollmentStatus.COMPLETED and is_passing(e.grade)
    }


def check_prerequisites(enrollments: Iterable[Enrollment], course: Course) -> PrerequisiteResult:
    done = passed_courses(enrollments)
    missing = [code for code in course.prerequisites if code not in done]
    return PrerequisiteResult(satisfied=not missing, missing=missing)


def prerequisite_chain(code: str, catalog: Mapping[str, Course]) -> list[str]:
    """All transitive prerequisites of ``code`` in a valid study order (deepest first)."""
    order: list[str] = []
    visiting: list[str] = []
    done: set[str] = set()

    def visit(current: str) -> None:
        if current in done:
            return
        if current in visiting:
            start = visiting.index(current)
            raise PrerequisiteCycleError(visiting[start:] + [current])
        course = catalog.get(current)
        if course is None:
            raise UnknownCourseError(current)
        visiting.append(current)
        for prereq in course.prerequisites:
            visit(prereq)
        visiting.pop()
        done.add(current)
        order.append(current)

    visit(code)
    return order[:-1]


def find_cycles(catalog: Mapping[str, Course]) -> list[list[str]]:
    cycles = []
    for code in sorted(catalog):
        try:
            prerequisite_chain(code, catalog)
        except PrerequisiteCycleError as exc:
            if sorted(exc.cycle[:-1]) not in [sorted(c[:-1]) for c in cycles]:
                cycles.append(exc.cycle)
    return cycles


def unlocked_courses(enrollments: Iterable[Enrollment], catalog: Mapping[str, Course]) -> list[str]:
    """Courses the student could take next: not yet passed and all prerequisites met."""
    history = list(enrollments)
    done = passed_courses(history)
    return sorted(
        code
        for code, course in catalog.items()
        if code not in done and check_prerequisites(history, course).satisfied
    )
