from unierp.reports.audit import degree_audit
from unierp.reports.standing import Standing, academic_standing, deans_list, on_deans_list, standing_for_student
from unierp.reports.transcript import build_transcript

from factories import graded


def test_transcript_structure(demo_store):
    transcript = build_transcript(demo_store, "S2024001")
    assert [t["term"] for t in transcript["terms"]] == ["2024-FALL", "2025-SPRING", "2025-FALL"]
    assert transcript["earned_credits"] == 24
    assert transcript["cgpa"] == 3.57


def test_transcript_applies_repeat_policy(demo_store):
    transcript = build_transcript(demo_store, "S2024002")
    # CS102 was failed then passed with B-; only the B- counts in the CGPA.
    assert transcript["cgpa"] == 2.53
    assert transcript["attempted_credits"] == 19


def test_academic_standing_rules():
    assert academic_standing(3.0, 20) is Standing.GOOD
    assert academic_standing(1.9, 20) is Standing.PROBATION
    assert academic_standing(1.4, 20, previously_on_probation=True) is Standing.DISMISSAL
    assert academic_standing(0.5, 4) is Standing.GOOD


def test_standing_for_student(demo_store):
    assert standing_for_student(demo_store, "S2024001")["standing"] == "good"
    # C + D over 8 credits -> 1.5 CGPA
    assert standing_for_student(demo_store, "S2025001")["standing"] == "probation"


def test_deans_list_threshold():
    assert on_deans_list(3.8, 12)
    assert not on_deans_list(3.8, 8)


def test_deans_list_for_term(store):
    for code, grade in (("CS101", "A"), ("CS102", "A"), ("MA101", "A-"), ("HS101", "B")):
        store.add_enrollment(graded(code, "2026-SPRING", grade))
    assert deans_list(store, "2026-SPRING") == ["S1"]
    assert deans_list(store, "2025-FALL") == []


def test_degree_audit(demo_store):
    audit = degree_audit(demo_store, "S2024001")
    assert audit.missing_core == []
    assert audit.remaining_credits == 16
    assert not audit.eligible_to_graduate
