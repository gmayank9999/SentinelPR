import pytest

from unierp.core.errors import ValidationError
from unierp.exam.grades import compute_gpa, effective_attempts, gpa_trend, term_gpas
from unierp.exam.grading_scale import is_passing, letter_for_score, validate_grade

from factories import graded


@pytest.mark.parametrize(
    "score, letter",
    [(100, "A"), (93, "A"), (92.99, "A-"), (90, "A-"), (85, "B"), (72, "C-"), (60, "D"), (59.9, "F"), (0, "F")],
)
def test_letter_for_score(score, letter):
    assert letter_for_score(score) == letter


def test_letter_for_score_rejects_out_of_range():
    with pytest.raises(ValidationError):
        letter_for_score(101)


def test_validate_grade_normalises_case():
    assert validate_grade(" b+ ") == "B+"
    with pytest.raises(ValidationError):
        validate_grade("E")


def test_passing_grades():
    assert is_passing("D")
    assert is_passing("P")
    assert not is_passing("F")
    assert not is_passing("W")
    assert not is_passing(None)


def test_gpa_is_credit_weighted(catalog):
    history = [graded("CS101", "2025-FALL", "A"), graded("MA101", "2025-FALL", "C")]
    # (4*4 + 2*3) / 7 = 3.142857...
    assert compute_gpa(history, catalog) == 3.14


def test_gpa_rounds_half_up(catalog):
    history = [graded("CS101", "2025-FALL", "B"), graded("MA101", "2025-FALL", "A")]
    # (3.0*4 + 4.0*3) / 7 = 3.428... -> 3.43, not truncated to 3.42
    assert compute_gpa(history, catalog) == 3.43


def test_gpa_ignores_withdrawals(catalog):
    history = [graded("CS101", "2025-FALL", "A"), graded("MA101", "2025-FALL", "W")]
    assert compute_gpa(history, catalog) == 4.0


def test_gpa_of_empty_record_is_zero(catalog):
    assert compute_gpa([], catalog) == 0.0


def test_repeat_policy_keeps_latest_attempt(catalog):
    history = [graded("CS101", "2025-SPRING", "F"), graded("CS101", "2025-FALL", "B")]
    assert len(effective_attempts(history)) == 1
    assert compute_gpa(history, catalog) == 3.0


def test_term_gpa_counts_every_attempt_in_that_term(catalog):
    history = [graded("CS101", "2025-SPRING", "F"), graded("MA101", "2025-SPRING", "A")]
    assert compute_gpa(history, catalog, term="2025-SPRING") == 1.71


def test_term_gpas_are_ordered(catalog):
    history = [graded("CS102", "2026-SPRING", "A"), graded("CS101", "2025-FALL", "C")]
    assert list(term_gpas(history, catalog)) == ["2025-FALL", "2026-SPRING"]


def test_gpa_trend(catalog):
    history = [graded("CS101", "2025-FALL", "C"), graded("CS102", "2026-SPRING", "A")]
    assert gpa_trend(history, catalog) == "improving"
    assert gpa_trend(history[:1], catalog) == "steady"
