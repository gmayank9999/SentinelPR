"""FastAPI application exposing UniERP over HTTP."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from unierp import __version__
from unierp.api.schemas import AttendanceIn, DropIn, GradeIn, LateFeeIn, RegistrationIn, ScoresIn, StudentIn
from unierp.attendance.tracker import condoned_percentage, is_exam_eligible, mark_attendance, shortage_report
from unierp.core.errors import UniErpError
from unierp.core.store import Store
from unierp.exam.assessment import final_grade, weighted_score
from unierp.exam.grades import compute_gpa, gpa_trend, term_gpas
from unierp.fees.calculator import apply_late_fee, term_fee_breakdown
from unierp.fees.invoices import generate_invoice, outstanding_balance
from unierp.ops.faults import FaultConfig
from unierp.ops.metrics import Metrics
from unierp.registration.credits import CREDIT_MODES, calculateStudentCredits
from unierp.registration.enrollment import drop, record_grade, register
from unierp.registration.prerequisites import unlocked_courses
from unierp.reports.audit import degree_audit
from unierp.reports.standing import deans_list, standing_for_student
from unierp.reports.transcript import build_transcript
from unierp.seed import build_demo_store
from unierp.students.service import create_student, search_students

UNINSTRUMENTED = {"/metrics", "/metrics.json", "/health"}


def create_app(store: Store | None = None, faults: FaultConfig | None = None) -> FastAPI:
    store = store or build_demo_store()
    faults = faults or FaultConfig.from_env()
    metrics = Metrics()
    version = os.getenv("UNIERP_VERSION", __version__)

    app = FastAPI(title="UniERP", version=__version__)
    app.state.store, app.state.metrics, app.state.faults = store, metrics, faults

    @app.middleware("http")
    async def instrument(request: Request, call_next):
        path = request.url.path
        if path in UNINSTRUMENTED:
            return await call_next(request)
        started = time.perf_counter()
        affected = faults.triggered()
        request.state.fault = affected
        if affected and faults.kind == "slow":
            await asyncio.sleep(faults.delay_ms / 1000)
        if affected and faults.kind == "memory":
            faults.leak()
        if affected and faults.kind == "errors":
            response = JSONResponse({"detail": "injected fault"}, status_code=500)
        else:
            response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", path)
        metrics.observe(f"{request.method} {template}", response.status_code, (time.perf_counter() - started) * 1000)
        response.headers["X-UniERP-Version"] = version
        return response

    @app.exception_handler(UniErpError)
    async def domain_error(_: Request, exc: UniErpError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    # ops -----------------------------------------------------------------
    @app.get("/health")
    def health():
        return {"status": "ok", "version": version, "fault": faults.kind}

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus():
        return metrics.prometheus(version)

    @app.get("/metrics.json")
    def metrics_json():
        return {"version": version, **metrics.snapshot()}

    # students ------------------------------------------------------------
    @app.get("/students")
    def list_students(q: str = "", program: str | None = None):
        return [_student(s) for s in search_students(store, q, program)]

    @app.post("/students", status_code=201)
    def add_student(body: StudentIn):
        return _student(create_student(store, **body.model_dump()))

    @app.get("/students/{student_id}")
    def get_student(student_id: str):
        return _student(store.get_student(student_id))

    @app.get("/students/{student_id}/credits")
    def student_credits(student_id: str, term: str | None = None):
        store.get_student(student_id)
        history = store.enrollments_for(student_id)
        return {m: calculateStudentCredits(history, store.catalog, term=term, mode=m) for m in CREDIT_MODES}

    @app.get("/students/{student_id}/gpa")
    def student_gpa(request: Request, student_id: str):
        store.get_student(student_id)
        history = store.enrollments_for(student_id)
        cgpa = compute_gpa(history, store.catalog)
        if faults.kind == "wrong" and getattr(request.state, "fault", False):
            cgpa = round(min(cgpa + 0.3, 4.0), 2)
        return {"cgpa": cgpa, "terms": term_gpas(history, store.catalog), "trend": gpa_trend(history, store.catalog)}

    @app.get("/students/{student_id}/transcript")
    def transcript(student_id: str):
        return build_transcript(store, student_id)

    @app.get("/students/{student_id}/standing")
    def standing(student_id: str):
        return standing_for_student(store, student_id)

    @app.get("/students/{student_id}/audit")
    def audit(student_id: str):
        return degree_audit(store, student_id).as_dict()

    @app.get("/students/{student_id}/eligible-courses")
    def eligible_courses(student_id: str):
        store.get_student(student_id)
        return unlocked_courses(store.enrollments_for(student_id), store.catalog)

    @app.get("/students/{student_id}/fees/{term}")
    def fees(student_id: str, term: str):
        student = store.get_student(student_id)
        items = term_fee_breakdown(student, store.enrollments_for(student_id), store.catalog, term)
        return {"items": {k: str(v) for k, v in items.items()}, "total": str(sum(items.values()))}

    # courses & registration ---------------------------------------------
    @app.get("/courses")
    def courses():
        return [_course(c) for c in sorted(store.catalog.values(), key=lambda c: c.code)]

    @app.get("/courses/{code}")
    def course(code: str):
        return _course(store.get_course(code))

    @app.post("/registrations", status_code=201)
    def add_registration(body: RegistrationIn):
        return _enrollment(register(store, body.student_id, body.course_code, body.term))

    @app.post("/registrations/{student_id}/{code}/{term}/drop")
    def drop_registration(student_id: str, code: str, term: str, body: DropIn):
        return _enrollment(drop(store, student_id, code, term, today=body.today, term_start=body.term_start))

    @app.put("/registrations/{student_id}/{code}/{term}/grade")
    def grade_registration(student_id: str, code: str, term: str, body: GradeIn):
        return _enrollment(record_grade(store, student_id, code, term, body.grade))

    # exams ----------------------------------------------------------------
    @app.post("/exams/grade")
    def exam_grade(body: ScoresIn):
        components = {"internal": body.internal, "final": body.final}
        return {
            "score": weighted_score(components),
            "grade": final_grade(components, attendance_eligible=body.attendance_eligible),
        }

    @app.get("/honours/{term}")
    def honours(term: str):
        return {"term": term, "deans_list": deans_list(store, term)}

    # attendance -----------------------------------------------------------
    @app.post("/attendance", status_code=201)
    def add_attendance(body: AttendanceIn):
        record = mark_attendance(
            store, body.student_id, body.course_code, body.session_date, present=body.present, excused=body.excused
        )
        return asdict(record)

    @app.get("/attendance/{student_id}/{code}")
    def attendance(student_id: str, code: str):
        records = store.attendance_for(student_id, code)
        return {"sessions": len(records), "condoned_pct": condoned_percentage(records), "eligible": is_exam_eligible(records)}

    @app.get("/attendance/shortage/{code}/{term}")
    def shortage(code: str, term: str):
        return [asdict(entry) for entry in shortage_report(store, code, term)]

    # fees -----------------------------------------------------------------
    @app.post("/fees/late-fee")
    def late_fee(body: LateFeeIn):
        return {"payable": str(apply_late_fee(body.amount, body.due_date, body.paid_on))}

    @app.post("/invoices/{student_id}/{term}", status_code=201)
    def invoice(student_id: str, term: str):
        inv = generate_invoice(store, student_id, term, date.today())
        return {"id": inv.id, "total": str(inv.total), "due_date": inv.due_date.isoformat()}

    @app.get("/invoices/{invoice_id}/balance")
    def balance(invoice_id: str):
        b = outstanding_balance(store, invoice_id, date.today())
        return {"payable": str(b.payable), "paid": str(b.paid), "outstanding": str(b.outstanding), "status": b.status}

    return app


def _student(s) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "email": s.email,
        "program": s.program,
        "year": s.year,
        "status": s.status.value,
    }


def _course(c) -> dict:
    return {
        "code": c.code,
        "title": c.title,
        "credits": c.credits,
        "department": c.department,
        "prerequisites": list(c.prerequisites),
        "capacity": c.capacity,
    }


def _enrollment(e) -> dict:
    return {
        "student_id": e.student_id,
        "course_code": e.course_code,
        "term": e.term,
        "status": e.status.value,
        "grade": e.grade,
    }


app = create_app()
