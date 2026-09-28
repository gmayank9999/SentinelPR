"""UniERP's development history, oldest commit first.

``seed_history.py`` turns this into a git repository plus issue/PR tracker data. Edits are
(path, before, after) pairs; ``before=""`` means the ``after`` text was introduced by that
commit. Function-sized blocks are sliced out of the current source with :func:`block` so
the timeline never drifts from the real code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

PRIYA = ("Priya Sharma", "priya.sharma@unierp.dev")
ARJUN = ("Arjun Menon", "arjun.menon@unierp.dev")
NEHA = ("Neha Kulkarni", "neha.kulkarni@unierp.dev")
VIKRAM = ("Vikram Joshi", "vikram.joshi@unierp.dev")


@dataclass(frozen=True)
class Edit:
    path: str
    before: str
    after: str


@dataclass
class Step:
    subject: str
    author: tuple[str, str]
    adds: tuple[str, ...] = ()
    edits: list[Edit] = field(default_factory=list)
    pr: int | None = None
    closes: tuple[int, ...] = ()
    body: str = ""
    pr_body: str = ""
    reviews: tuple[tuple[str, str], ...] = ()

    def full_message(self) -> str:
        subject = f"{self.subject} (#{self.pr})" if self.pr else self.subject
        parts = [subject]
        if self.body:
            parts.append(self.body)
        if self.closes:
            parts.append("\n".join(f"Closes #{n}." for n in self.closes))
        return "\n\n".join(parts)


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    author: str
    comments: tuple[tuple[str, str], ...] = ()
    opened_after: int = 0  # for issues without a closing PR: index of the step they follow
    closed: bool = False


def _source(path: str) -> str:
    return (PROJECT / path).read_text(encoding="utf-8").replace("\r\n", "\n")


def block(path: str, header: str) -> str:
    """The top-level definition starting with ``header``, including the blank lines before it."""
    text = _source(path)
    start = text.find("\n" + header)
    if start < 0:
        raise SystemExit(f"{header!r} not found in {path}")
    start += 1
    if text[start - 3 : start] != "\n\n\n":
        raise SystemExit(f"{header!r} in {path} is not preceded by two blank lines")
    following = re.compile(r"\n\n\n(?=\S)").search(text, start)
    end = following.start() if following else len(text.rstrip("\n"))
    return text[start - 3 : end]


def section(path: str, heading: str) -> str:
    """A Markdown section from ``heading`` to the end of the file, with its leading blank line."""
    text = _source(path)
    start = text.find("\n" + heading) + 1
    if start <= 0 or text[start - 2 : start] != "\n\n":
        raise SystemExit(f"{heading!r} not found in {path}")
    return text[start - 2 : len(text.rstrip("\n"))]


def added(path: str, header: str) -> Edit:
    return Edit(path, "", block(path, header))


def line(path: str, before: str, after: str) -> Edit:
    return Edit(path, before, after)


def version_bump(old: str, new: str) -> list[Edit]:
    return [
        Edit("unierp/__init__.py", f'__version__ = "{old}"', f'__version__ = "{new}"'),
        Edit("pyproject.toml", f'version = "{old}"', f'version = "{new}"'),
    ]


# ---------------------------------------------------------------------------
# Tracker issues
# ---------------------------------------------------------------------------

ISSUES = [
    Issue(
        12,
        "Grace marks at the pass boundary",
        "The Examination Committee resolution EC-2025-04 allows up to 2 grace marks, but only to "
        "lift a student to the pass mark (60). Grace must never move a student between higher "
        "bands, e.g. from B to B+.",
        ("enhancement", "exam"),
        "Dr. S. Raman",
        (
            ("Arjun Menon", "So 58.0-59.99 becomes 60, and everything else is untouched?"),
            ("Dr. S. Raman", "Correct. Anything below 58 is a genuine fail."),
        ),
    ),
    Issue(
        17,
        "Letter grade wrong for scores exactly on a cutoff",
        "A student with a weighted score of exactly 90.0 received B+ instead of A-. Same for 93.0 "
        "(A- instead of A). The cutoff should be inclusive.",
        ("bug", "exam"),
        "Neha Kulkarni",
        (("Arjun Menon", "The comparison in letter_for_score uses `>`; it should be `>=`."),),
    ),
    Issue(
        23,
        "Prerequisite check accepts courses that are still in progress",
        "A student registered for CS102 while still taking CS101 in the same term. The "
        "prerequisite check treated the ongoing CS101 enrollment as passed. Withdrawn (W) "
        "courses were also accepted.",
        ("bug", "registration"),
        "Registrar Office",
        (
            (
                "Arjun Menon",
                "passed_courses() counts ENROLLED enrollments and any non-F grade. Only COMPLETED "
                "courses with a passing grade should count.",
            ),
        ),
    ),
    Issue(
        29,
        "Late fee charged on the last day of the grace period",
        "Payment made exactly 3 days after the due date was charged a 2% late fee. The fee "
        "policy says payments within 3 days are not charged.",
        ("bug", "fees"),
        "Finance Office",
    ),
    Issue(
        31,
        "GPA on transcripts is truncated, registrar rounds half-up",
        "Student with 24 quality points over 7 credits shows 3.42 in the ERP but 3.43 on the "
        "printed transcript. The registrar rounds half-up to two decimal places; the ERP "
        "truncates.",
        ("bug", "exam"),
        "Registrar Office",
        (
            (
                "Arjun Menon",
                "compute_gpa uses int(x * 100) / 100. Switching to Decimal with ROUND_HALF_UP so we "
                "match the registrar exactly and avoid float artefacts.",
            ),
        ),
    ),
    Issue(
        35,
        "Why is the attendance threshold 75%?",
        "Parents keep asking why exam eligibility needs 75% attendance and why medical leave does "
        "not fully count. Is this configurable?",
        ("question", "attendance"),
        "Student Affairs",
        (
            (
                "Neha Kulkarni",
                "Both numbers come from the Academic Council rulebook (section 7.3): 75% minimum "
                "attendance for exam eligibility, and medically excused absences may be condoned "
                "up to 10 percentage points. They are constants in attendance/tracker.py "
                "(MIN_ATTENDANCE_PCT, MAX_CONDONATION_PCT) and are intentionally not configurable "
                "per department.",
            ),
        ),
        opened_after=24,
        closed=True,
    ),
    Issue(
        40,
        "Late fee grows without limit",
        "An invoice paid four months late was charged 34% extra. Policy caps the late fee at 10% "
        "of the invoice.",
        ("bug", "fees"),
        "Finance Office",
    ),
    Issue(
        42,
        "Round fee totals down to whole rupees",
        "Finance does not bill paise. Invoices currently round half-up (e.g. 1019.50 -> 1020), "
        "which caused several disputes. Finance policy FIN-7: all student-facing amounts are "
        "rounded down to the whole rupee, in the student's favour.",
        ("enhancement", "fees", "finance"),
        "Finance Office",
        (
            ("Vikram Joshi", "Will switch round_fee() to ROUND_DOWN. This applies to tuition and late fees."),
            ("Finance Office", "Yes, every amount printed on an invoice."),
        ),
    ),
    Issue(
        45,
        "Repeated course: the earlier attempt counts in CGPA",
        "Diya failed CS102 in 2025-SPRING and passed it in 2025-FALL, but her CGPA still uses the "
        "F. The repeat policy says only the latest attempt counts.",
        ("bug", "exam"),
        "Diya Nair",
        (
            (
                "Arjun Menon",
                "effective_attempts() compares term strings: '2025-SPRING' > '2025-FALL' "
                "lexicographically, so spring wins. Needs a proper term ordering.",
            ),
        ),
    ),
    Issue(
        47,
        "Credit load limits for honours and probation students",
        "Why can some students register for 24 credits while others are capped at 16?",
        ("question", "registration"),
        "Student Affairs",
        (
            (
                "Neha Kulkarni",
                "Senate rule R-12: the normal cap is 20 credits per term. Students with CGPA >= 3.5 "
                "may take up to 24 (honours overload); students on probation or below 2.0 are "
                "limited to 16 so they can focus on recovering. See credit_load_limits().",
            ),
        ),
        opened_after=30,
        closed=True,
    ),
    Issue(
        53,
        "Students with no attendance sessions yet shown as ineligible",
        "At the start of term every student shows 0% attendance and 'not eligible' because no "
        "sessions have been recorded.",
        ("bug", "attendance"),
        "Neha Kulkarni",
    ),
    Issue(
        56,
        "Condonation exceeds the 10% cap",
        "A student with 70% attendance and 30% medically excused absences was shown at 100%. "
        "Condonation is limited to 10 percentage points.",
        ("bug", "attendance"),
        "Student Affairs",
    ),
    Issue(
        60,
        "Course section accepts one student over capacity",
        "HS101 has capacity 100 and ended up with 101 students registered.",
        ("bug", "registration"),
        "Registrar Office",
    ),
    Issue(
        66,
        "Charge late fees on overdue invoice balances",
        "The balance endpoint shows the billed total even after the due date. It should include "
        "the late fee that would be charged if the student paid today.",
        ("enhancement", "fees"),
        "Finance Office",
    ),
    Issue(
        70,
        "New students put on academic probation after their first term",
        "A first-year student who took a single 4-credit course and got a D is on probation. "
        "Standing should only be assessed once a student has attempted at least 8 credits.",
        ("bug", "reports"),
        "Dr. S. Raman",
    ),
    Issue(
        78,
        "Duplicate student accounts with different email casing",
        "Two records exist for the same student: nisha@uni.edu and Nisha@uni.edu.",
        ("bug", "students"),
        "Registrar Office",
    ),
    Issue(
        81,
        "Fully paid invoice still shows an outstanding late fee",
        "A student who paid the full invoice on time is shown as 'partial' with an outstanding "
        "late fee once the due date passes.",
        ("bug", "fees"),
        "Finance Office",
        (
            (
                "Vikram Joshi",
                "outstanding_balance() computes the late fee as of today even when the invoice "
                "was settled before the due date. The reference date should be the settlement date.",
            ),
        ),
    ),
    Issue(
        87,
        "Dropped course still billed in term tuition",
        "Rohan dropped HS101 inside the add/drop window but his invoice still charges tuition for "
        "it, and the registration page says he is at his credit limit.",
        ("bug", "registration", "fees"),
        "Finance Office",
        (
            (
                "Neha Kulkarni",
                "calculateStudentCredits' in_progress mode counts every non-completed enrollment, "
                "which includes DROPPED (and FAILED). It should only count ENROLLED, and dropped "
                "enrollments should never count in any mode. This also affects the credit-load "
                "check in registration and anything else that calls calculateStudentCredits.",
            ),
            ("Priya Sharma", "Please add a regression test - this function is used everywhere."),
        ),
    ),
    Issue(
        90,
        "Support pass/fail courses in GPA",
        "Some electives are graded P/F. P should count toward earned credits but not GPA.",
        ("enhancement", "exam"),
        "Dr. S. Raman",
        opened_after=52,
    ),
    Issue(
        92,
        "Attendance export for department heads",
        "Department heads want a CSV export of the shortage report.",
        ("enhancement", "attendance"),
        "Student Affairs",
        opened_after=53,
    ),
]


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------

T_GRADES = "tests/test_grades.py"
T_PREREQ = "tests/test_prerequisites.py"
T_ATT = "tests/test_attendance.py"
T_FEES = "tests/test_fees.py"
T_ASSESS = "tests/test_assessment.py"

STEPS: list[Step] = [
    Step("Initial project layout", PRIYA, adds=("pyproject.toml", "requirements.txt", ".gitignore", "unierp/__init__.py")),
    Step("Add domain error hierarchy", PRIYA, adds=("unierp/core/__init__.py", "unierp/core/errors.py")),
    Step("Add academic term parsing helpers", PRIYA, adds=("unierp/core/terms.py",)),
    Step("Add core domain entities", PRIYA, adds=("unierp/core/domain.py",)),
    Step("Add in-memory store", PRIYA, adds=("unierp/core/store.py",)),
    Step("Add letter grade scale", ARJUN, adds=("unierp/exam/__init__.py", "unierp/exam/grading_scale.py")),
    Step(
        "Add calculateStudentCredits with earned, attempted and in-progress modes",
        NEHA,
        adds=("unierp/registration/__init__.py", "unierp/registration/credits.py"),
        body="Single place for credit accounting so GPA, tuition and the credit-load check agree.",
    ),
    Step("Set up pytest with shared fixtures", NEHA, adds=("tests/conftest.py", "tests/factories.py")),
    Step("Add tests for credit calculation", NEHA, adds=("tests/test_credits.py",)),
    Step("Add prerequisite checks and study-order resolution", ARJUN, adds=("unierp/registration/prerequisites.py",), pr=3),
    Step("Add prerequisite tests", ARJUN, adds=(T_PREREQ,)),
    Step("Add credit-weighted GPA with repeat policy", ARJUN, adds=("unierp/exam/grades.py",), pr=5),
    Step("Add GPA and grade scale tests", ARJUN, adds=(T_GRADES,)),
    Step(
        "Add course registration with add/drop window",
        NEHA,
        adds=("unierp/registration/enrollment.py",),
        pr=8,
        body="Checks student status, duplicates, capacity, prerequisites, timetable clashes and credit load.",
        reviews=((PRIYA[0], "Looks good. Can we make the add/drop window a constant?"), (NEHA[0], "Done.")),
    ),
    Step("Add registration tests", NEHA, adds=("tests/test_enrollment.py",)),
    Step("Add weighted assessment and final grades", ARJUN, adds=("unierp/exam/assessment.py",), pr=10),
    Step("Add assessment tests", ARJUN, adds=(T_ASSESS,)),
    Step("Add student records service", VIKRAM, adds=("unierp/students/__init__.py", "unierp/students/service.py"), pr=11),
    Step("Add student service tests", VIKRAM, adds=("tests/test_students.py",)),
    Step("Release 1.0.0", PRIYA, edits=version_bump("0.1.0", "1.0.0")),
    Step(
        "Apply grace marks at the pass boundary",
        ARJUN,
        pr=13,
        closes=(12,),
        body="Implements EC-2025-04: up to 2 grace marks, only to reach the pass mark.",
        edits=[
            line("unierp/exam/assessment.py", "", '\nMAX_GRACE_MARKS = 2.0'),
            added("unierp/exam/assessment.py", "def apply_grace_marks("),
            line(
                "unierp/exam/assessment.py",
                "    return letter_for_score(weighted_score(components, weights))",
                "    return letter_for_score(apply_grace_marks(weighted_score(components, weights)))",
            ),
            line(T_ASSESS, "import final_grade, weighted_score", "import apply_grace_marks, final_grade, weighted_score"),
            added(T_ASSESS, "def test_grace_marks_only_near_pass_mark("),
            line(T_ASSESS, "", '\n    assert final_grade({"internal": 60, "final": 58}) == "D"'),
        ],
    ),
    Step(
        "Fix letter grade for scores exactly on a cutoff",
        ARJUN,
        pr=18,
        closes=(17,),
        edits=[
            line("unierp/exam/grading_scale.py", "        if score > cutoff:", "        if score >= cutoff:"),
            line(
                T_GRADES,
                '    [(100, "A"), (92.99, "A-"), (85, "B"), (72, "C-"), (59.9, "F"), (0, "F")],',
                '    [(100, "A"), (93, "A"), (92.99, "A-"), (90, "A-"), (85, "B"), (72, "C-"), (60, "D"), (59.9, "F"), (0, "F")],',
            ),
        ],
    ),
    Step(
        "Add attendance tracking and exam eligibility",
        NEHA,
        adds=("unierp/attendance/__init__.py", "unierp/attendance/tracker.py"),
        pr=20,
        body="75% attendance required to sit the final; medical absences can be condoned.",
    ),
    Step("Add attendance tests", NEHA, adds=(T_ATT,)),
    Step(
        "Only count completed courses as satisfied prerequisites",
        ARJUN,
        pr=24,
        closes=(23,),
        edits=[
            line(
                "unierp/registration/prerequisites.py",
                '    """Courses the student has completed with a passing grade."""',
                '    """Courses the student has completed with a passing grade.\n\n'
                "    In-progress courses do not count: a prerequisite must be finished before the\n"
                '    dependent course starts (issue #23).\n    """',
            ),
            line(
                "unierp/registration/prerequisites.py",
                '        if e.status in (EnrollmentStatus.COMPLETED, EnrollmentStatus.ENROLLED) and e.grade != "F"\n    }',
                "        if e.status is EnrollmentStatus.COMPLETED and is_passing(e.grade)\n    }",
            ),
            added(T_PREREQ, "def test_in_progress_prerequisite_does_not_count("),
            line(T_PREREQ, "from unierp.core.domain import Course\n", "from unierp.core.domain import Course, Enrollment\n"),
        ],
        reviews=((NEHA[0], "Good catch on W grades as well."),),
    ),
    Step(
        "Add fee calculator with tuition and late fees",
        VIKRAM,
        adds=("unierp/fees/__init__.py", "unierp/fees/calculator.py"),
        pr=26,
        body="Tuition is charged per in-progress credit, scholarships apply to tuition only.",
    ),
    Step("Add fee calculator tests", VIKRAM, adds=(T_FEES,)),
    Step(
        "Do not charge late fees inside the grace period",
        VIKRAM,
        pr=30,
        closes=(29,),
        edits=[
            line("unierp/fees/calculator.py", "    if days_late < GRACE_DAYS:", "    if days_late <= GRACE_DAYS:"),
            added(T_FEES, "def test_no_late_fee_within_grace_period("),
        ],
    ),
    Step(
        "Round GPA half-up with Decimal instead of truncating",
        ARJUN,
        pr=32,
        closes=(31,),
        edits=[
            line(
                "unierp/exam/grades.py",
                '"""GPA computation."""',
                '"""GPA computation.\n\n'
                "GPAs are computed with ``Decimal`` and rounded half-up to two places, matching what\n"
                'the registrar prints on transcripts (issue #31).\n"""',
            ),
            line("unierp/exam/grades.py", "from decimal import Decimal", "from decimal import ROUND_HALF_UP, Decimal"),
            line("unierp/exam/grades.py", "", '\nTWO_PLACES = Decimal("0.01")\n'),
            line(
                "unierp/exam/grades.py",
                "    return int(float(points) / attempted * 100) / 100",
                "    return float((points / Decimal(attempted)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))",
            ),
            added(T_GRADES, "def test_gpa_rounds_half_up("),
        ],
        reviews=((PRIYA[0], "Please double check against the registrar's sample transcripts."),),
    ),
    Step(
        "Add per-term GPA breakdown and trend",
        ARJUN,
        edits=[
            added("unierp/exam/grades.py", "def term_gpas("),
            added("unierp/exam/grades.py", "def gpa_trend("),
            line(
                T_GRADES,
                "from unierp.exam.grades import compute_gpa, effective_attempts\n",
                "from unierp.exam.grades import compute_gpa, effective_attempts, gpa_trend, term_gpas\n",
            ),
            added(T_GRADES, "def test_term_gpas_are_ordered("),
            added(T_GRADES, "def test_gpa_trend("),
        ],
    ),
    Step(
        "Add invoices, payments and balances",
        VIKRAM,
        adds=("unierp/fees/invoices.py",),
        pr=38,
        edits=[],
    ),
    Step("Add invoice tests", VIKRAM, adds=("tests/test_invoices.py",)),
    Step("Release 1.1.0", PRIYA, edits=version_bump("1.0.0", "1.1.0")),
    Step(
        "Cap late fees at 10% of the invoice",
        VIKRAM,
        pr=41,
        closes=(40,),
        edits=[
            line("unierp/fees/calculator.py", "", 'LATE_FEE_CAP = Decimal("0.10")\n'),
            line("unierp/fees/calculator.py", "2% per started week late.", "2% per started week late, capped at 10%."),
            line(
                "unierp/fees/calculator.py",
                "    return LATE_FEE_RATE_PER_WEEK * weeks",
                "    return min(LATE_FEE_RATE_PER_WEEK * weeks, LATE_FEE_CAP)",
            ),
            added(T_FEES, "def test_late_fee_is_capped("),
        ],
    ),
    Step(
        "Round fee totals down to the whole rupee",
        VIKRAM,
        pr=43,
        closes=(42,),
        body="Finance policy FIN-7: amounts are rounded in the student's favour.",
        edits=[
            line(
                "unierp/fees/calculator.py",
                '"""Fee calculation. All amounts are in rupees."""',
                '"""Fee calculation.\n\n'
                "All amounts are in rupees. Totals are rounded *down* to the whole rupee: the finance\n"
                "office does not bill paise and rounding in the student's favour avoids disputes over\n"
                'fractional amounts (issue #42).\n"""',
            ),
            line("unierp/fees/calculator.py", "from decimal import ROUND_HALF_UP, Decimal", "from decimal import ROUND_DOWN, Decimal"),
            line(
                "unierp/fees/calculator.py",
                '    return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)',
                '    return amount.quantize(Decimal("1"), rounding=ROUND_DOWN)',
            ),
            added(T_FEES, "def test_round_fee_rounds_down("),
            added(T_FEES, "def test_late_fee_result_is_rounded_down("),
        ],
        reviews=(("Finance Office", "Confirmed with the bursar, thanks."),),
    ),
    Step(
        "Use real term ordering when applying the repeat policy",
        ARJUN,
        pr=46,
        closes=(45,),
        edits=[
            line(
                "unierp/exam/grades.py",
                "        if current is None or e.term > current.term:",
                "        if current is None or term_sort_key(e.term) > term_sort_key(current.term):",
            ),
            added(T_GRADES, "def test_repeat_policy_keeps_latest_attempt("),
        ],
    ),
    Step(
        "Detect prerequisite cycles in the catalog",
        NEHA,
        edits=[
            added("unierp/registration/prerequisites.py", "def find_cycles("),
            line(T_PREREQ, "", "    find_cycles,\n"),
            line(T_PREREQ, "", "\n    assert len(find_cycles(cyclic)) == 1"),
        ],
    ),
    Step("Add official transcript report", PRIYA, adds=("unierp/reports/__init__.py", "unierp/reports/transcript.py"), pr=49),
    Step("Add academic standing rules", PRIYA, adds=("unierp/reports/standing.py",), pr=51),
    Step("Add degree audit", ARJUN, adds=("unierp/reports/audit.py",), pr=52),
    Step(
        "Treat terms with no recorded sessions as full attendance",
        NEHA,
        pr=54,
        closes=(53,),
        edits=[
            line(
                "unierp/attendance/tracker.py",
                "    records = list(records)\n    present = sum(1 for r in records if r.present)\n"
                "    return round(100.0 * present / max(len(records), 1), 1)",
                "    records = list(records)\n    if not records:\n        return 100.0\n"
                "    present = sum(1 for r in records if r.present)\n"
                "    return round(100.0 * present / len(records), 1)",
            ),
            added(T_ATT, "def test_no_sessions_counts_as_full_attendance("),
        ],
    ),
    Step(
        "Cap medical condonation at 10 percentage points",
        NEHA,
        pr=57,
        closes=(56,),
        edits=[
            line(
                "unierp/attendance/tracker.py",
                "    credit = 100.0 * excused / len(records)",
                "    credit = min(100.0 * excused / len(records), MAX_CONDONATION_PCT)",
            ),
            added(T_ATT, "def test_condonation_is_capped("),
        ],
    ),
    Step(
        "Add demo dataset and report tests",
        PRIYA,
        adds=("unierp/seed.py", "tests/test_reports.py"),
        pr=58,
        edits=[
            line(
                "tests/conftest.py",
                "from unierp.core.store import Store\n",
                "from unierp.core.store import Store\nfrom unierp.seed import build_demo_store\n",
            ),
            added("tests/conftest.py", "@pytest.fixture\ndef demo_store("),
        ],
    ),
    Step("Release 1.2.0", PRIYA, edits=version_bump("1.1.0", "1.2.0")),
    Step(
        "Reject registrations once a section is full",
        NEHA,
        pr=61,
        closes=(60,),
        edits=[
            line(
                "unierp/registration/enrollment.py",
                "    if store.section_size(course_code, term) > course.capacity:",
                "    if store.section_size(course_code, term) >= course.capacity:",
            ),
            line("tests/test_enrollment.py", "from datetime import date\n", "from dataclasses import replace\nfrom datetime import date\n"),
            added("tests/test_enrollment.py", "def test_register_rejects_full_section("),
        ],
    ),
    Step(
        "Suggest courses a student can take next",
        ARJUN,
        edits=[
            added("unierp/registration/prerequisites.py", "def unlocked_courses("),
            line(T_PREREQ, "", "    unlocked_courses,\n"),
            added(T_PREREQ, "def test_unlocked_courses("),
        ],
    ),
    Step(
        "Add dean's list for each term",
        PRIYA,
        pr=63,
        edits=[
            added("unierp/reports/standing.py", "def deans_list("),
            line(
                "tests/test_reports.py",
                "import Standing, academic_standing, on_deans_list",
                "import Standing, academic_standing, deans_list, on_deans_list",
            ),
            added("tests/test_reports.py", "def test_deans_list_for_term("),
        ],
    ),
    Step(
        "Add student search by name, id and program",
        VIKRAM,
        pr=64,
        edits=[
            added("unierp/students/service.py", "def search_students("),
            line(
                "tests/test_students.py",
                "import change_status, create_student\n",
                "import change_status, create_student, search_students\n",
            ),
            added("tests/test_students.py", "def test_search("),
        ],
    ),
    Step(
        "Add international student tuition surcharge",
        VIKRAM,
        edits=[
            line("unierp/fees/calculator.py", "", 'INTERNATIONAL_SURCHARGE = Decimal("0.25")\n'),
            line(
                "unierp/fees/calculator.py",
                "",
                "\n    if student.is_international:\n        tuition += tuition * INTERNATIONAL_SURCHARGE",
            ),
            added(T_FEES, "def test_international_surcharge("),
        ],
    ),
    Step(
        "Include late fees in outstanding invoice balances",
        VIKRAM,
        pr=67,
        closes=(66,),
        edits=[
            line(
                "unierp/fees/invoices.py",
                "from unierp.fees.calculator import term_fee_breakdown",
                "from unierp.fees.calculator import apply_late_fee, term_fee_breakdown",
            ),
            line(
                "unierp/fees/invoices.py",
                '    """Balance on ``as_of``."""',
                '    """Balance on ``as_of``. Late fees accrue on the invoice total once the due date passes."""',
            ),
            line(
                "unierp/fees/invoices.py",
                "    payable = invoice.total",
                "    payable = apply_late_fee(invoice.total, invoice.due_date, as_of)",
            ),
            added("tests/test_invoices.py", "def test_balance_accrues_late_fee("),
        ],
    ),
    Step(
        "Only assess standing after 8 attempted credits",
        PRIYA,
        pr=71,
        closes=(70,),
        edits=[
            line(
                "unierp/reports/standing.py",
                "",
                "# Standing is only assessed once a student has a meaningful record.\nMIN_CREDITS_FOR_STANDING = 8\n",
            ),
            line(
                "unierp/reports/standing.py",
                "    if cgpa >= PROBATION_THRESHOLD or attempted_credits == 0:\n        return Standing.GOOD\n",
                "    if attempted_credits < MIN_CREDITS_FOR_STANDING:\n        return Standing.GOOD\n"
                "    if cgpa >= PROBATION_THRESHOLD:\n        return Standing.GOOD\n",
            ),
            line("tests/test_reports.py", "", "\n    assert academic_standing(0.5, 4) is Standing.GOOD"),
        ],
    ),
    Step(
        "Add attendance shortage report",
        NEHA,
        edits=[
            line("unierp/attendance/tracker.py", "", "from dataclasses import dataclass\n"),
            line(
                "unierp/attendance/tracker.py",
                "from unierp.core.domain import AttendanceRecord\n",
                "from unierp.core.domain import AttendanceRecord, EnrollmentStatus\n",
            ),
            added("unierp/attendance/tracker.py", "@dataclass\nclass ShortageEntry"),
            added("unierp/attendance/tracker.py", "def shortage_report("),
            line(T_ATT, "", "    shortage_report,\n"),
            line(T_ATT, "from unierp.core.domain import AttendanceRecord\n", "from unierp.core.domain import AttendanceRecord, Enrollment\n"),
            added(T_ATT, "def test_shortage_report("),
        ],
    ),
    Step(
        "Add result summary statistics for exams",
        ARJUN,
        edits=[
            line("unierp/exam/assessment.py", "", "import statistics\n"),
            line("unierp/exam/assessment.py", "from collections.abc import Mapping\n", "from collections.abc import Mapping, Sequence\n"),
            line("unierp/exam/assessment.py", "", "from dataclasses import dataclass\n"),
            added("unierp/exam/assessment.py", "@dataclass\nclass ResultSummary"),
            added("unierp/exam/assessment.py", "def summarise_results("),
            line(
                T_ASSESS,
                "import apply_grace_marks, final_grade, weighted_score",
                "import apply_grace_marks, final_grade, summarise_results, weighted_score",
            ),
            added(T_ASSESS, "def test_summarise_results("),
        ],
    ),
    Step(
        "Add request metrics and fault injection for rollout testing",
        PRIYA,
        adds=("unierp/ops/__init__.py", "unierp/ops/metrics.py", "unierp/ops/faults.py"),
        pr=73,
    ),
    Step(
        "Expose UniERP over a FastAPI HTTP API",
        PRIYA,
        adds=("unierp/api/__init__.py", "unierp/api/schemas.py", "unierp/api/app.py", "tests/test_api.py"),
        pr=75,
        reviews=((ARJUN[0], "Could we map domain errors to status codes in one handler?"), (PRIYA[0], "Added.")),
    ),
    Step("Add Dockerfile and service README", PRIYA, adds=("Dockerfile", "README.md")),
    Step("Release 1.3.0", PRIYA, edits=version_bump("1.2.0", "1.3.0")),
    Step(
        "Compare student emails case-insensitively",
        VIKRAM,
        pr=79,
        closes=(78,),
        edits=[
            line(
                "unierp/students/service.py",
                "    if any(s.email == email for s in store.students.values()):",
                "    if any(s.email.lower() == email.lower() for s in store.students.values()):",
            ),
            added("tests/test_students.py", "def test_duplicate_email_rejected("),
        ],
    ),
    Step(
        "Stop accruing late fees once an invoice is settled",
        VIKRAM,
        pr=82,
        closes=(81,),
        edits=[
            line(
                "unierp/fees/invoices.py",
                "    payable = apply_late_fee(invoice.total, invoice.due_date, as_of)",
                "    settled_on = max((p.paid_on for p in payments), default=as_of)\n"
                "    reference = as_of if paid < invoice.total else settled_on\n"
                "    payable = apply_late_fee(invoice.total, invoice.due_date, reference)",
            ),
            added("tests/test_invoices.py", "def test_full_payment_on_time("),
        ],
    ),
    Step(
        "Document operations endpoints and fault injection",
        PRIYA,
        edits=[Edit("README.md", "", section("README.md", "## Operations"))],
    ),
    Step(
        "Add container healthcheck",
        PRIYA,
        edits=[
            line(
                "Dockerfile",
                "",
                'HEALTHCHECK --interval=5s --timeout=2s --retries=5 \\\n'
                '  CMD python -c "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8000/health\')"\n',
            )
        ],
    ),
    Step(
        "Exclude dropped enrollments from every credit mode",
        NEHA,
        pr=88,
        closes=(87,),
        body="in_progress counted anything not COMPLETED, so dropped (and failed) courses were billed "
        "and counted toward the credit-load limit.",
        edits=[
            line(
                "unierp/registration/credits.py",
                "",
                "\n\n    Dropped enrollments never count toward any mode (see issue #87).",
            ),
            line(
                "unierp/registration/credits.py",
                "            continue\n        course = catalog.get(enrollment.course_code)",
                "            continue\n        if enrollment.status is EnrollmentStatus.DROPPED:\n"
                "            continue\n        course = catalog.get(enrollment.course_code)",
            ),
            line(
                "unierp/registration/credits.py",
                "        elif enrollment.status is not EnrollmentStatus.COMPLETED:",
                "        elif enrollment.status is EnrollmentStatus.ENROLLED:",
            ),
            line(
                "tests/test_credits.py",
                "import pytest\n\nfrom unierp.core.errors",
                "import pytest\n\nfrom unierp.core.domain import Enrollment, EnrollmentStatus\nfrom unierp.core.errors",
            ),
            added("tests/test_credits.py", "def test_dropped_courses_never_count("),
        ],
        reviews=((PRIYA[0], "Thanks for the regression test - this function is used everywhere."),),
    ),
    Step("Release 1.4.0", PRIYA, edits=version_bump("1.3.0", "1.4.0")),
]

