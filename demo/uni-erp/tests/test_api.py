import pytest
from fastapi.testclient import TestClient

from unierp.api.app import create_app
from unierp.ops.faults import FaultConfig
from unierp.seed import build_demo_store


@pytest.fixture
def client():
    return TestClient(create_app(build_demo_store(), FaultConfig()))


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["fault"] == "none"


def test_list_and_get_students(client):
    assert len(client.get("/students").json()) == 8
    assert client.get("/students/S2024001").json()["name"] == "Aarav Mehta"
    assert client.get("/students/NOPE").status_code == 404


def test_create_student(client):
    response = client.post(
        "/students",
        json={"name": "New Person", "email": "new@uni.edu", "program": "BSC-MATH", "admitted_on": "2026-07-01"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "S2026002"
    assert client.post("/students", json={**response.json(), "admitted_on": "2026-07-01"}).status_code == 422


def test_credits_endpoint(client):
    assert client.get("/students/S2024001/credits").json() == {"earned": 24, "attempted": 24, "in_progress": 8}


def test_gpa_endpoint(client):
    body = client.get("/students/S2024001/gpa").json()
    assert body["cgpa"] == 3.57
    assert list(body["terms"]) == ["2024-FALL", "2025-SPRING", "2025-FALL"]


def test_transcript_standing_audit(client):
    assert client.get("/students/S2024004/transcript").json()["cgpa"] == 3.91
    assert client.get("/students/S2025001/standing").json()["standing"] == "probation"
    assert client.get("/students/S2024001/audit").json()["remaining_credits"] == 16


def test_registration_flow(client):
    response = client.post("/registrations", json={"student_id": "S2026001", "course_code": "HS101", "term": "2026-FALL"})
    assert response.status_code == 201
    clash = client.post("/registrations", json={"student_id": "S2026001", "course_code": "CS202", "term": "2026-FALL"})
    assert clash.status_code == 409
    graded = client.put("/registrations/S2026001/HS101/2026-FALL/grade", json={"grade": "A"})
    assert graded.json()["status"] == "completed"


def test_fees_and_late_fee(client):
    fees = client.get("/students/S2024002/fees/2026-FALL").json()
    assert fees["items"]["tuition"] == "5000"
    late = client.post("/fees/late-fee", json={"amount": "10000", "due_date": "2026-09-01", "paid_on": "2026-09-09"})
    assert late.json()["payable"] == "10400"


def test_exam_grade(client):
    assert client.post("/exams/grade", json={"internal": 90, "final": 95}).json()["grade"] == "A"


def test_attendance_endpoints(client):
    body = client.get("/attendance/S2024001/CS301").json()
    assert body["sessions"] == 12
    assert isinstance(client.get("/attendance/shortage/CS301/2026-FALL").json(), list)


def test_metrics_are_recorded(client):
    client.get("/students")
    client.get("/students/NOPE")
    snapshot = client.get("/metrics.json").json()
    assert snapshot["requests"] == 2
    assert snapshot["routes"]["GET /students/{student_id}"]["requests"] == 1
    assert "unierp_requests_total" in client.get("/metrics").text


def test_injected_errors():
    client = TestClient(create_app(build_demo_store(), FaultConfig(kind="errors", rate=1.0)))
    assert client.get("/students").status_code == 500
    assert client.get("/metrics.json").json()["errors"] == 1
