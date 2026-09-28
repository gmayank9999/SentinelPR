from datetime import date

import pytest

from unierp.core.domain import Course, Student
from unierp.core.store import Store
from unierp.seed import build_demo_store


@pytest.fixture
def catalog():
    return {
        "CS101": Course("CS101", "Programming", 4, "CSE", slot="A", lab=True),
        "CS102": Course("CS102", "Data Structures", 4, "CSE", prerequisites=("CS101",), slot="B"),
        "CS201": Course("CS201", "Algorithms", 4, "CSE", prerequisites=("CS102", "MA101"), slot="C"),
        "MA101": Course("MA101", "Calculus", 3, "MATH", slot="A"),
        "HS101": Course("HS101", "Writing", 2, "HSS", slot="H"),
    }


@pytest.fixture
def store(catalog):
    s = Store()
    s.add_courses(catalog.values())
    s.add_student(Student("S1", "Test Student", "test@uni.edu", "BTECH-CSE", 2, date(2025, 7, 1)))
    return s


@pytest.fixture
def demo_store():
    return build_demo_store()
