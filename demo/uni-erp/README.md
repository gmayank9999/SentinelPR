# UniERP

A compact university ERP service (FastAPI) used as the demo target for SentinelPR.

| Module | What it does | Key functions |
|---|---|---|
| `students` | Student records and status transitions | `create_student`, `change_status` |
| `registration` | Credits, prerequisites, add/drop | `calculateStudentCredits`, `check_prerequisites`, `register` |
| `exam` | Grading scale, assessment, GPA | `letter_for_score`, `final_grade`, `compute_gpa` |
| `attendance` | Attendance and exam eligibility | `condoned_percentage`, `is_exam_eligible` |
| `fees` | Tuition, late fees, invoices | `apply_late_fee`, `term_fee_breakdown` |
| `reports` | Transcript, standing, degree audit | `build_transcript`, `academic_standing`, `degree_audit` |

`calculateStudentCredits` is deliberately central: GPA, the credit-load check, tuition, the
transcript, standing and the degree audit all depend on it.

## Running

```bash
pip install -r requirements.txt
pytest                                   # test suite
uvicorn unierp.api.app:app --reload      # http://localhost:8000/docs
```

## Operations

* `GET /health` — version and active fault
* `GET /metrics` — Prometheus text; `GET /metrics.json` — the same as JSON with p50/p95

Fault injection for canary rehearsals is driven by environment variables:

| Variable | Values |
|---|---|
| `UNIERP_FAULT` | `none`, `slow`, `errors`, `memory`, `wrong` |
| `UNIERP_FAULT_RATE` | probability a request is affected (default `0.05`) |
| `UNIERP_FAULT_DELAY_MS` | added latency for `slow` (default `400`) |
| `UNIERP_VERSION` | label reported in `/health` and metrics |
