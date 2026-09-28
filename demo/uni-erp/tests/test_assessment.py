import pytest

from unierp.core.errors import ValidationError
from unierp.exam.assessment import apply_grace_marks, final_grade, summarise_results, weighted_score


def test_weighted_score_default_weights():
    assert weighted_score({"internal": 80, "final": 70}) == 74.0


def test_weighted_score_validates_weights():
    with pytest.raises(ValidationError):
        weighted_score({"internal": 80}, {"internal": 0.5})


def test_weighted_score_requires_all_components():
    with pytest.raises(ValidationError, match="final"):
        weighted_score({"internal": 80})


def test_grace_marks_only_near_pass_mark():
    assert apply_grace_marks(58.5) == 60.0
    assert apply_grace_marks(57.0) == 57.0
    assert apply_grace_marks(89.0) == 89.0


def test_final_grade():
    assert final_grade({"internal": 95, "final": 92}) == "A"
    assert final_grade({"internal": 60, "final": 58}) == "D"


def test_debarred_student_fails():
    assert final_grade({"internal": 95, "final": 95}, attendance_eligible=False) == "F"


def test_summarise_results():
    summary = summarise_results([40, 60, 80, 100])
    assert summary.mean == 70.0
    assert summary.pass_rate == 75.0
    with pytest.raises(ValidationError):
        summarise_results([])
