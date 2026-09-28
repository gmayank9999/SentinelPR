"""Hidden oracle tests for UniERP.

These tests are never visible to SentinelPR. The benchmark runs them (together with the
base revision's own tests) against every generated PR to decide, by execution, whether the
PR changed behaviour. They pin exact boundaries and values that the visible suite leaves
loosely specified, which is what lets us build "all tests pass, but it is still a bug" PRs.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from unierp.core.domain import Course, Enrollment, EnrollmentStatus, Student, StudentStatus
from unierp.core.store import Store

CATALOG = {
    "A1": Course("A1", "Intro", 4, "X", slot="S1", lab=True),
    "A2": Course("A2", "Next", 4, "X", prerequisites=("A1",), slot="S2"),
    "A3": Course("A3", "Deep", 4, "X", prerequisites=("A2",), slot="S3"),
    "B1": Course("B1", "Side", 3, "Y", slot="S4"),
    "C1": Course("C1", "Short", 2, "Z", slot="S5"),
    "D1": Course("D1", "Extra", 4, "Z", slot="S6"),
    "E1": Course("E1", "More", 4, "Z", slot="S7"),
    "F1": Course("F1", "Last", 4, "Z", slot="S8"),
}


def done(code, grade, term="2025-FALL", sid="S1"):
    status = EnrollmentStatus.FAILED if grade == "F" else EnrollmentStatus.COMPLETED
    return Enrollment(sid, code, term, status, grade)


def fresh_store(status=StudentStatus.ACTIVE):
    store = Store()
    store.add_courses(CATALOG.values())
    store.add_student(Student("S1", "Hidden Student", "h@uni.edu", "BTECH-CSE", 2, date(2025, 7, 1), status=status))
    return store


# --- registration / credits --------------------------------------------------------

class TestCredits:
    def test_honours_and_probation_limits(self):
        from unierp.registration.credits import credit_load_limits

        assert credit_load_limits(3.5) == (12, 24)
        assert credit_load_limits(3.49) == (12, 20)
        assert credit_load_limits(2.0) == (12, 20)
        assert credit_load_limits(1.99) == (12, 16)
        assert credit_load_limits(0.0) == (12, 20)
        assert credit_load_limits(4.0, on_probation=True) == (12, 16)

    def test_full_time_boundary(self):
        from unierp.registration.credits import is_full_time

        assert is_full_time(12) and not is_full_time(11)

    def test_earned_modes_for_every_grade_kind(self):
        from unierp.registration.credits import calculateStudentCredits

        history = [done("A1", "D"), done("A2", "W"), done("B1", "P"), done("C1", "F"), done("D1", "I")]
        assert calculateStudentCredits(history, CATALOG, mode="earned") == 4 + 3
        assert calculateStudentCredits(history, CATALOG, mode="attempted") == 4 + 2

    def test_in_progress_ignores_failed_and_dropped(self):
        from unierp.registration.credits import calculateStudentCredits

        history = [
            Enrollment("S1", "A1", "2026-FALL"),
            Enrollment("S1", "B1", "2026-FALL", EnrollmentStatus.DROPPED),
            Enrollment("S1", "C1", "2026-FALL", EnrollmentStatus.FAILED, "F"),
        ]
        assert calculateStudentCredits(history, CATALOG, mode="in_progress") == 4
        assert calculateStudentCredits(history, CATALOG, mode="in_progress", term="2026-SPRING") == 0


class TestPrerequisites:
    def test_withdrawn_and_failed_do_not_satisfy(self):
        from unierp.registration.prerequisites import check_prerequisites

        assert not check_prerequisites([done("A1", "W")], CATALOG["A2"]).satisfied
        assert check_prerequisites([done("A1", "D")], CATALOG["A2"]).satisfied

    def test_chain_is_complete_and_ordered(self):
        from unierp.registration.prerequisites import prerequisite_chain

        assert prerequisite_chain("A3", CATALOG) == ["A1", "A2"]
        assert prerequisite_chain("A1", CATALOG) == []


class TestRegistration:
    def test_d_grade_may_be_repeated(self):
        from unierp.registration.enrollment import register

        store = fresh_store()
        store.add_enrollment(done("A1", "D"))
        assert register(store, "S1", "A1", "2026-FALL").status is EnrollmentStatus.ENROLLED

    def test_credit_limit_is_inclusive(self):
        from unierp.core.errors import RegistrationError
        from unierp.registration.enrollment import register

        store = fresh_store()
        for code in ("A1", "B1", "D1", "E1"):  # 4 + 3 + 4 + 4 = 15
            register(store, "S1", code, "2026-FALL")
        register(store, "S1", "C1", "2026-FALL")  # 17
        store.catalog["G1"] = Course("G1", "Three", 3, "Z", slot="S9")
        register(store, "S1", "G1", "2026-FALL")  # exactly 20 is allowed
        with pytest.raises(RegistrationError):
            register(store, "S1", "F1", "2026-FALL")

    def test_probation_students_capped_at_16(self):
        from unierp.core.errors import RegistrationError
        from unierp.registration.enrollment import register

        store = fresh_store(StudentStatus.PROBATION)
        for code in ("A1", "D1", "E1", "F1"):
            register(store, "S1", code, "2026-FALL")
        with pytest.raises(RegistrationError):
            register(store, "S1", "C1", "2026-FALL")

    def test_capacity_is_exact(self):
        from unierp.core.errors import RegistrationError
        from unierp.registration.enrollment import register

        store = fresh_store()
        store.catalog["B1"] = replace(CATALOG["B1"], capacity=2)
        store.add_enrollment(Enrollment("S7", "B1", "2026-FALL"))
        register(store, "S1", "B1", "2026-FALL")
        store.add_student(Student("S2", "Other", "o@uni.edu", "BTECH-CSE", 1, date(2026, 7, 1)))
        with pytest.raises(RegistrationError):
            register(store, "S2", "B1", "2026-FALL")

    def test_drop_window_edges(self):
        from unierp.registration.enrollment import drop

        start = date(2026, 8, 3)
        store = fresh_store()
        store.add_enrollment(Enrollment("S1", "A1", "2026-FALL"))
        store.add_enrollment(Enrollment("S1", "B1", "2026-FALL"))
        assert drop(store, "S1", "A1", "2026-FALL", today=start + timedelta(days=14), term_start=start).status is EnrollmentStatus.DROPPED
        assert drop(store, "S1", "B1", "2026-FALL", today=start + timedelta(days=15), term_start=start).grade == "W"


# --- exams ---------------------------------------------------------------------------

class TestGrading:
    @pytest.mark.parametrize("score, letter", [
        (93, "A"), (92.99, "A-"), (90, "A-"), (89.99, "B+"), (87, "B+"), (86.99, "B"), (83, "B"), (82.99, "B-"),
        (80, "B-"), (79.99, "C+"), (77, "C+"), (76.99, "C"), (73, "C"), (72.99, "C-"), (70, "C-"), (69.99, "D"),
        (60, "D"), (59.99, "F"),
    ])
    def test_every_cutoff(self, score, letter):
        from unierp.exam.grading_scale import letter_for_score

        assert letter_for_score(score) == letter

    def test_grade_points_and_flags(self):
        from unierp.core.errors import ValidationError
        from unierp.exam.grading_scale import counts_toward_gpa, grade_points, is_passing

        assert grade_points("A-") == 3.7 and grade_points("D") == 1.0
        assert counts_toward_gpa("F") and not counts_toward_gpa("P") and not counts_toward_gpa("W")
        assert is_passing("P") and not is_passing("I")
        with pytest.raises(ValidationError):
            grade_points("W")

    def test_gpa_values(self):
        from unierp.exam.grades import compute_gpa

        history = [done("A1", "A"), done("B1", "C+"), done("C1", "F")]
        # (16 + 6.9 + 0) / 9 = 2.5444 -> 2.54
        assert compute_gpa(history, CATALOG) == 2.54
        assert compute_gpa(history + [done("D1", "B-", "2026-SPRING")], CATALOG, term="2026-SPRING") == 2.7

    def test_half_up_rounding_edge(self):
        from unierp.exam.grades import compute_gpa

        # (13.2 + 14.8 + 9.2 + 6.8 + 16 + 8) / 24 = 2.8333 -> 2.83
        grades = ["B+", "A-", "C+", "C-", "A", "C"]
        history = [done(code, g) for code, g in zip(["A1", "A2", "A3", "D1", "E1", "F1"], grades)]
        assert compute_gpa(history, CATALOG) == 2.83

    def test_trend_thresholds(self):
        from unierp.exam.grades import gpa_trend

        improving = [done("A1", "C+", "2025-FALL"), done("A2", "B-", "2026-SPRING")]  # 2.3 -> 2.7
        declining = [done("A1", "B-", "2025-FALL"), done("A2", "C+", "2026-SPRING")]
        small_drop = [done("A1", "B", "2025-FALL"), done("A2", "B-", "2026-SPRING")]  # 3.0 -> 2.7
        assert gpa_trend(improving, CATALOG) == "improving"
        assert gpa_trend(declining, CATALOG) == "declining"
        assert gpa_trend(small_drop, CATALOG) == "declining"
        assert gpa_trend([done("A1", "B", "2025-FALL"), done("A2", "B", "2026-SPRING")], CATALOG) == "steady"

    def test_repeat_policy_keeps_ungraded(self):
        from unierp.exam.grades import effective_attempts

        history = [done("A1", "F", "2025-SPRING"), done("A1", "C", "2025-FALL"), Enrollment("S1", "A2", "2026-FALL")]
        kept = effective_attempts(history)
        assert len(kept) == 2 and {e.grade for e in kept} == {"C", None}


class TestAssessment:
    def test_grace_boundaries(self):
        from unierp.exam.assessment import apply_grace_marks

        assert apply_grace_marks(58.0) == 60.0
        assert apply_grace_marks(57.99) == 57.99
        assert apply_grace_marks(60.0) == 60.0
        assert apply_grace_marks(59.5, max_grace=0.4) == 59.5

    def test_weighted_score_rounding_and_custom_weights(self):
        from unierp.exam.assessment import weighted_score

        assert weighted_score({"internal": 77.777, "final": 88.888}) == 84.44
        assert weighted_score({"a": 50, "b": 100}, {"a": 0.5, "b": 0.5}) == 75.0

    def test_summary_statistics(self):
        from unierp.exam.assessment import summarise_results

        summary = summarise_results([59.99, 60, 70, 100])
        assert (summary.median, summary.pass_rate, summary.lowest) == (65.0, 75.0, 59.99)


# --- attendance ----------------------------------------------------------------------

def records(present, excused, absent):
    from unierp.core.domain import AttendanceRecord

    day, out = date(2026, 8, 3), []
    for flag, exc, count in ((True, False, present), (False, True, excused), (False, False, absent)):
        for _ in range(count):
            out.append(AttendanceRecord("S1", "A1", day, flag, exc))
            day += timedelta(days=1)
    return out


class TestAttendance:
    def test_threshold_is_inclusive(self):
        from unierp.attendance.tracker import is_exam_eligible

        assert is_exam_eligible(records(75, 0, 25))
        assert not is_exam_eligible(records(74, 0, 26))

    def test_condonation_exactly_capped(self):
        from unierp.attendance.tracker import condoned_percentage

        assert condoned_percentage(records(60, 20, 20)) == 70.0
        assert condoned_percentage(records(60, 5, 35)) == 65.0
        assert condoned_percentage(records(95, 5, 0)) == 100.0


# --- fees ------------------------------------------------------------------------------

DUE = date(2026, 9, 1)


class TestFees:
    @pytest.mark.parametrize("days, expected", [(0, "10000"), (3, "10000"), (4, "10200"), (7, "10200"), (8, "10400"), (35, "11000"), (36, "11000"), (400, "11000")])
    def test_late_fee_schedule(self, days, expected):
        from unierp.fees.calculator import apply_late_fee

        assert apply_late_fee(Decimal("10000"), DUE, DUE + timedelta(days=days)) == Decimal(expected)

    def test_rounding_is_always_down(self):
        from unierp.fees.calculator import apply_late_fee, round_fee

        assert round_fee(Decimal("100.999")) == Decimal("100")
        assert round_fee(Decimal("100.5")) == Decimal("100")
        assert apply_late_fee(Decimal("1234.56"), DUE, DUE + timedelta(days=10)) == Decimal("1283")

    def test_surcharge_then_scholarship(self):
        from unierp.fees.calculator import term_fee_breakdown

        student = Student("S1", "X", "x@uni.edu", "BTECH-CSE", 1, date(2026, 1, 1), scholarship_pct=40, is_international=True)
        items = term_fee_breakdown(student, [Enrollment("S1", "B1", "2026-FALL")], CATALOG, "2026-FALL")
        # 3 credits * 2500 = 7500, +25% = 9375, -40% = 5625
        assert items["tuition"] == Decimal("5625")
        assert "lab" not in items

    def test_full_scholarship(self):
        from unierp.fees.calculator import apply_scholarship

        assert apply_scholarship(Decimal("9999"), 100) == Decimal("0")
        assert apply_scholarship(Decimal("9999"), 0) == Decimal("9999")

    def test_breakdown_counts_only_current_enrolments(self):
        from unierp.fees.calculator import term_fee_breakdown

        student = Student("S1", "X", "x@uni.edu", "BTECH-CSE", 1, date(2026, 1, 1))
        history = [Enrollment("S1", "A1", "2026-FALL"), Enrollment("S1", "B1", "2026-FALL", EnrollmentStatus.DROPPED), done("C1", "A", "2026-FALL")]
        items = term_fee_breakdown(student, history, CATALOG, "2026-FALL")
        assert items["tuition"] == Decimal("10000") and items["lab"] == Decimal("2000")

    def test_invoice_settled_late_includes_fee_at_settlement(self):
        from unierp.fees.invoices import generate_invoice, outstanding_balance, record_payment

        store = fresh_store()
        store.add_enrollment(Enrollment("S1", "B1", "2026-FALL"))
        invoice = generate_invoice(store, "S1", "2026-FALL", date(2026, 8, 1))
        assert invoice.due_date == date(2026, 8, 31)
        record_payment(store, invoice.id, Decimal("5000"), date(2026, 9, 5))
        partial = outstanding_balance(store, invoice.id, date(2026, 9, 5))
        assert partial.status == "partial" and partial.payable == Decimal("9996")
        record_payment(store, invoice.id, Decimal("4996"), date(2026, 9, 5))
        settled = outstanding_balance(store, invoice.id, date(2027, 1, 1))
        assert settled.status == "paid" and settled.payable == Decimal("9996")


# --- reports ---------------------------------------------------------------------------

class TestReports:
    def test_standing_boundaries(self):
        from unierp.reports.standing import Standing, academic_standing

        assert academic_standing(2.0, 8) is Standing.GOOD
        assert academic_standing(1.99, 8) is Standing.PROBATION
        assert academic_standing(1.5, 20, previously_on_probation=True) is Standing.PROBATION
        assert academic_standing(1.49, 20, previously_on_probation=True) is Standing.DISMISSAL
        assert academic_standing(1.0, 7) is Standing.GOOD

    def test_deans_list_boundary(self):
        from unierp.reports.standing import on_deans_list

        assert on_deans_list(3.7, 12)
        assert not on_deans_list(3.69, 20)
        assert not on_deans_list(4.0, 11)

    def test_audit_eligible(self):
        from unierp.reports.audit import degree_audit

        store = fresh_store()
        store.catalog.update({c: Course(c, c, 8, "X") for c in ("CS101", "CS102", "CS201", "CS202", "MA101")})
        for code in ("CS101", "CS102", "CS201", "CS202", "MA101"):
            store.add_enrollment(done(code, "B"))
        audit = degree_audit(store, "S1")
        assert audit.remaining_credits == 0 and audit.eligible_to_graduate and audit.cgpa == 3.0

    def test_transcript_per_term_earned(self):
        from unierp.reports.transcript import build_transcript

        store = fresh_store()
        for e in (done("A1", "A", "2025-FALL"), done("B1", "F", "2025-FALL"), done("B1", "C", "2026-SPRING")):
            store.add_enrollment(e)
        transcript = build_transcript(store, "S1")
        assert [t["earned_credits"] for t in transcript["terms"]] == [4, 3]
        assert transcript["cgpa"] == round((16 + 6) / 7, 2)


# --- students & terms -------------------------------------------------------------------

class TestStudents:
    def test_year_bounds_and_ids(self):
        from unierp.core.errors import ValidationError
        from unierp.students.service import create_student

        store = fresh_store()
        a = create_student(store, name="Ab Cd", email="a@uni.edu", program="BSC-MATH", admitted_on=date(2027, 7, 1), year=5)
        b = create_student(store, name="Ef Gh", email="b@uni.edu", program="BSC-MATH", admitted_on=date(2027, 7, 1), year=1)
        assert (a.id, b.id) == ("S2027001", "S2027002")
        with pytest.raises(ValidationError):
            create_student(store, name="Ij Kl", email="c@uni.edu", program="BSC-MATH", admitted_on=date(2027, 7, 1), year=0)

    def test_same_status_is_noop(self):
        from unierp.students.service import change_status

        store = fresh_store(StudentStatus.WITHDRAWN)
        assert change_status(store, "S1", StudentStatus.WITHDRAWN).status is StudentStatus.WITHDRAWN


class TestTerms:
    def test_ordering_and_next(self):
        from unierp.core.terms import is_before, parse_term, sort_terms

        assert str(parse_term("2026-fall").next()) == "2027-SPRING"
        assert sort_terms(["2026-FALL", "2026-SPRING", "2025-FALL", "2026-SUMMER"]) == ["2025-FALL", "2026-SPRING", "2026-SUMMER", "2026-FALL"]
        assert is_before("2026-SUMMER", "2026-FALL")

    def test_invalid_terms(self):
        from unierp.core.errors import ValidationError
        from unierp.core.terms import parse_term

        for bad in ("2026", "2026-WINTER", "1999-FALL", "abc-FALL"):
            with pytest.raises(ValidationError):
                parse_term(bad)
