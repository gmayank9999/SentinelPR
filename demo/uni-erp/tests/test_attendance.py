from datetime import date, timedelta

import pytest

from unierp.attendance.tracker import (
    attendance_percentage,
    condoned_percentage,
    is_exam_eligible,
    mark_attendance,
    shortage_report,
)
from unierp.core.domain import AttendanceRecord, Enrollment
from unierp.core.errors import ValidationError


def sessions(present, absent_excused=0, absent=0):
    day = date(2026, 8, 3)
    records = []
    for flag, excused, count in ((True, False, present), (False, True, absent_excused), (False, False, absent)):
        for _ in range(count):
            records.append(AttendanceRecord("S1", "CS101", day, flag, excused))
            day += timedelta(days=1)
    return records


def test_percentage():
    assert attendance_percentage(sessions(present=3, absent=1)) == 75.0


def test_no_sessions_counts_as_full_attendance():
    assert attendance_percentage([]) == 100.0
    assert is_exam_eligible([])


def test_condonation_is_capped():
    # 14/20 present = 70%, 6 excused = 30% but only 10 points are condoned.
    records = sessions(present=14, absent_excused=6)
    assert condoned_percentage(records) == 80.0


def test_eligibility_threshold():
    assert is_exam_eligible(sessions(present=15, absent=5))
    assert not is_exam_eligible(sessions(present=14, absent=6))
    assert is_exam_eligible(sessions(present=14, absent_excused=1, absent=5))


def test_mark_attendance_upserts(store):
    mark_attendance(store, "S1", "CS101", date(2026, 8, 3), present=False)
    mark_attendance(store, "S1", "CS101", date(2026, 8, 3), present=True)
    records = store.attendance_for("S1", "CS101")
    assert len(records) == 1 and records[0].present


def test_mark_attendance_rejects_present_and_excused(store):
    with pytest.raises(ValidationError):
        mark_attendance(store, "S1", "CS101", date(2026, 8, 3), present=True, excused=True)


def test_shortage_report(store):
    store.add_enrollment(Enrollment("S1", "CS101", "2026-FALL"))
    for offset in range(4):
        mark_attendance(store, "S1", "CS101", date(2026, 8, 3 + offset), present=offset == 0)
    report = shortage_report(store, "CS101", "2026-FALL")
    assert [entry.student_id for entry in report] == ["S1"]
    assert report[0].percentage == 25.0
