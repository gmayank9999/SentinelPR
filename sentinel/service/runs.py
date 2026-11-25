"""Analysis runs: queueing, execution, persistence and replay.

Layout under ``<workdir>/service/runs/<run_id>/``::

    meta.json      status and identity (repository, PR, SHAs, decision, config used)
    events.jsonl   the execution event log (see sentinel.service.events)
    report.json    the full run report the pipeline produced

Several analyses can be queued; at most ``service.max_concurrent_runs`` execute at once, and
two analyses of the *same* repository never overlap (they share an index and scratch space).
Reports from the command line (``.sentinel/runs``) are listed too, as imported runs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sentinel import __version__
from sentinel.config import Config
from sentinel.orchestrator.observe import PIPELINE_NODES, SKIP_REASONS, RunCancelled
from sentinel.publish.app_auth import AppAuthError
from sentinel.service.atomic import read_json, write_json
from sentinel.service.events import EventLog, redact
from sentinel.service.repos import DEMO_ID, RepoError, RepoRegistry

log = logging.getLogger("sentinel.service")
ACTIVE = ("queued", "running")


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


class RunManager:
    def __init__(self, cfg: Config, registry: RepoRegistry, service_dir: Path, max_workers: int | None = None):
        self.cfg = cfg
        self.registry = registry
        self.dir = service_dir / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)
        workers = int(max_workers or cfg.get("service.max_concurrent_runs", 2))
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sentinel-run")
        self._logs: dict[str, EventLog] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._repo_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()
        self.app_auth = None  # GitHubAppAuth, set by the service; creates Check Runs, which a personal token cannot
        self._publishing: set[str] = set()  # runs with a publish in flight: a double click must not post twice
        self._recover_interrupted()

    # identity & storage ----------------------------------------------------------------
    def _path(self, run_id: str) -> Path:
        if not run_id.replace("-", "").replace("_", "").isalnum():
            raise RepoError("Unknown run.", 404, "not_found")
        return self.dir / run_id

    def _write_meta(self, meta: dict) -> None:
        write_json(self._path(meta["run_id"]) / "meta.json", meta)

    def _read_meta(self, run_id: str) -> dict | None:
        try:
            path = self._path(run_id) / "meta.json"
        except RepoError:
            return None
        return read_json(path) if path.exists() else None

    def _recover_interrupted(self) -> None:
        """A run that was queued or running when the server stopped cannot continue; say so."""
        for path in self.dir.glob("*/meta.json"):
            meta = read_json(path)
            if meta.get("status") in ACTIVE:
                meta.update(status="failed", error="Interrupted: the SentinelPR server stopped while this analysis was running.", ended_at=time.time())
                self._write_meta(meta)
                EventLog.load(meta["run_id"], path.parent / "events.jsonl").emit("run", "run_failed", "failed", meta["error"])

    # submitting ------------------------------------------------------------------------------
    def submit(self, repo_id: str, ref: str, *, llm: bool = True, publish: bool = False) -> dict:
        repo = self.registry.get(repo_id)
        self.registry.execution_mode(repo)  # raises not_trusted when it can neither be sandboxed nor explicitly run on the host
        if repo["status"] == "unsupported":
            raise RepoError("This repository is not supported by the verifier (Python with pytest only).", 422, "unsupported")
        if repo["status"] in ("needs_seed", "seeding"):
            raise RepoError("The demo repository is not seeded yet.", 409, "needs_seed")
        with self._lock:
            for meta in self.list_runs():
                if meta["repo_id"] == repo_id and meta["pr"].get("ref") == str(ref) and meta["status"] in ACTIVE:
                    return {**meta, "deduplicated": True}
            run_id = new_run_id()
            meta = {"schema": "sentinelpr.run-meta/1", "run_id": run_id, "repo_id": repo_id, "repo_name": repo["full_name"],
                    "pr": {"ref": str(ref), "number": int(ref) if str(ref).isdigit() else None}, "status": "queued", "decision": None, "risk": None,
                    "created_at": time.time(), "started_at": None, "ended_at": None, "elapsed_s": None, "tokens": 0, "error": None,
                    "options": {"llm": bool(llm), "publish": bool(publish)}, "imported": False, "sentinel_version": __version__}
            self._write_meta(meta)
            events = EventLog(run_id, self._path(run_id) / "events.jsonl")
            self._logs[run_id] = events
            self._cancel[run_id] = threading.Event()
            events.emit("run", "run_queued", "queued", f"analysis queued for {repo['full_name']} ({ref})", {"repo_id": repo_id, "ref": str(ref), "llm": bool(llm)})
        self.pool.submit(self._execute, run_id)
        return meta

    def cancel(self, run_id: str) -> dict:
        meta = self.get_meta(run_id)
        if meta is None:
            raise RepoError("Unknown run.", 404, "not_found")
        if meta["status"] not in ACTIVE:
            raise RepoError(f"The run already {meta['status']}.", 409, "not_cancellable")
        event = self._cancel.get(run_id)
        if event is not None:
            event.set()
            from sentinel.verify import sandbox as sbx

            killed = sbx.cancel_run(run_id)  # stop the containers now; the run then ends before its next stage
            if killed and run_id in self._logs:
                self._logs[run_id].emit("run", "log", "info", f"stopped {killed} sandbox container(s)")
            if run_id in self._logs:
                self._logs[run_id].emit("run", "log", "info", "cancellation requested; the run stops before its next stage "
                                        "(a stage that is already executing tests finishes first)")
        return {**meta, "cancel_requested": True}

    # execution -------------------------------------------------------------------------------------
    def _execute(self, run_id: str) -> None:
        meta = self._read_meta(run_id)
        events, cancel = self._logs[run_id], self._cancel[run_id]
        lock = self._repo_locks.setdefault(meta["repo_id"], threading.Lock())
        if not lock.acquire(blocking=False):
            events.emit("run", "log", "queued", "waiting for another analysis of this repository to finish")
            lock.acquire()
        try:
            if cancel.is_set():
                return self._finish(meta, events, "cancelled", error=None)
            meta.update(status="running", started_at=time.time())
            self._write_meta(meta)
            events.emit("run", "run_started", "running", "analysis started", {"sentinel_version": __version__})
            intake_started = time.time()
            events.emit("intake", "stage_started", "running", "preparing the pull request")

            def emit(stage, kind, status, message, data=None):
                if stage == "intake" and kind == "stage_completed":
                    data = {**(data or {}), "duration_s": round(time.time() - intake_started, 3), "started_at": intake_started, "ended_at": time.time()}
                return events.emit(stage, kind, status, message, data)

            from sentinel.run import analyse

            spec = self.registry.prepare(meta["repo_id"], meta["pr"]["ref"], emit, run_id=run_id)
            cfg = spec.cfg.with_overrides({"llm": {"enabled": bool(meta["options"]["llm"])}})
            if spec.cfg.get("sandbox.mode") is None:
                raise RuntimeError("internal error: the analysis has no execution mode")
            meta["pr"] = {**meta["pr"], **{k: v for k, v in spec.info.items() if v is not None}, "base_sha": spec.base, "head_sha": spec.head}
            meta["execution"] = spec.info.get("execution", "host")
            self._write_meta(meta)
            report = analyse(cfg, base=spec.base, head=spec.head, pr=spec.pr, emit=emit, run_id=run_id, cancelled=cancel.is_set)
            (self._path(run_id) / "report.json").write_text(json.dumps(redact_report(report), default=str), encoding="utf-8")
            executed = {t["node"] for t in report.get("trace", [])}
            if meta["options"].get("publish"):
                meta["publish"] = publish_report(events, report, spec, meta, self.registry.get(meta["repo_id"]))
            for node in sorted(PIPELINE_NODES - executed | {"canary"} | ({"publish"} if not meta["options"].get("publish") else set())):
                reason = SKIP_REASONS.get(node) or ("The input guard rejected the PR before this stage." if report.get("rejected") else "Not reached.")
                events.emit(node, "stage_skipped", "skipped", reason, {"reason": reason})
            meta.update(decision=report["decision"], risk=report["risk"], tokens=report.get("tokens", 0), elapsed_s=report.get("elapsed_s"),
                        config=run_config(report), pr={**meta["pr"], "title": report["pr"]["title"], "author": report["pr"]["author"]})
            self._finish(meta, events, "completed")
        except RunCancelled as exc:
            self._finish(meta, events, "cancelled", error=f"Cancelled before {exc}.")
        except Exception as exc:  # noqa: BLE001 - every failure must end up on the run, not in a thread
            log.error("run %s failed:\n%s", run_id, traceback.format_exc())
            message = redact(f"{type(exc).__name__}: {exc}")
            self._close_open_stages(events, message)
            self._finish(meta, events, "failed", error=message, code=getattr(exc, "code", None))
        finally:
            lock.release()

    @staticmethod
    def _close_open_stages(events: EventLog, message: str) -> None:
        state: dict[str, str] = {}
        for e in events.since(0):
            if e["kind"] == "stage_started":
                state[e["stage"]] = "running"
            elif e["kind"] in ("stage_completed", "stage_failed", "stage_skipped"):
                state[e["stage"]] = "done"
        for stage, st in state.items():
            if st == "running":
                events.emit(stage, "stage_failed", "failed", message, {"error": message})

    def _finish(self, meta: dict, events: EventLog, status: str, error: str | None = None, code: str | None = None) -> None:
        meta.update(status=status, error=error, ended_at=time.time(), error_code=code)
        if meta.get("started_at"):
            meta["elapsed_s"] = meta.get("elapsed_s") or round(meta["ended_at"] - meta["started_at"], 2)
        self._write_meta(meta)
        kind = {"completed": "run_completed", "failed": "run_failed", "cancelled": "run_cancelled"}[status]
        events.emit("run", kind, {"completed": "completed", "failed": "failed", "cancelled": "cancelled"}[status],
                    error or f"analysis finished: {meta.get('decision')}", {"decision": meta.get("decision"), "risk": meta.get("risk"), "error": error})
        events.close()

    # queries ------------------------------------------------------------------------------------------
    def get_meta(self, run_id: str) -> dict | None:
        meta = self._read_meta(run_id)
        if meta is not None:
            return meta
        return next((m for m in self._legacy_runs() if m["run_id"] == run_id), None)

    def list_runs(self, repo_id: str | None = None, status: str | None = None, limit: int = 200) -> list[dict]:
        metas = []
        for path in self.dir.glob("*/meta.json"):
            try:
                metas.append(read_json(path))
            except (json.JSONDecodeError, PermissionError):
                continue
        metas += [m for m in self._legacy_runs() if m["run_id"] not in {x["run_id"] for x in metas}]
        if repo_id:
            metas = [m for m in metas if m["repo_id"] == repo_id]
        if status:
            metas = [m for m in metas if m["status"] == status]
        return sorted(metas, key=lambda m: m["created_at"] or 0, reverse=True)[:limit]

    def get_report(self, run_id: str) -> dict | None:
        try:
            path = self._path(run_id) / "report.json"
        except RepoError:
            return None
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return self._legacy_report(run_id)

    def get_log(self, run_id: str) -> EventLog | None:
        if run_id in self._logs:
            return self._logs[run_id]
        try:
            path = self._path(run_id) / "events.jsonl"
        except RepoError:
            return None
        if path.exists():
            return EventLog.load(run_id, path)
        report = self._legacy_report(run_id)
        return EventLog(run_id, None, reconstruct_events(report)) if report else None

    def activity(self, limit: int = 30, runs: int = 12) -> list[dict]:
        """A feed of what happened recently, read from the stored events of the latest runs."""
        items: list[dict] = []
        for meta in self.list_runs(limit=runs):
            log = self.get_log(meta["run_id"])
            if log is None:
                continue
            label = f"{meta['repo_name']} · " + (f"#{meta['pr']['number']}" if meta["pr"].get("number") else meta["pr"].get("ref") or "")
            base = {"run_id": meta["run_id"], "repo_id": meta["repo_id"], "pr_ref": meta["pr"].get("ref"), "pr_label": label}

            def add(e, kind, severity, title, detail=""):
                items.append({**base, "ts": e["ts"], "kind": kind, "severity": severity, "title": title, "detail": detail})

            for e in log.since(0):
                d = (e.get("data") or {}).get("detail") or {}
                k, stage = e["kind"], e["stage"]
                if k == "run_queued":
                    add(e, "analysis_queued", "info", "Analysis queued")
                elif k == "run_started":
                    add(e, "analysis_started", "info", "Analysis started")
                elif k == "stage_completed" and stage == "verify":
                    failed = (d.get("tests") or {}).get("failed") or []
                    if failed:
                        add(e, "tests_failed", "bad", f"Verifier found {len(failed)} failing test(s)", ", ".join(failed[:3]))
                    score = (d.get("mutation") or {}).get("score")
                    if score is not None:
                        add(e, "mutation", "warn" if score < 0.7 else "ok", f"Mutation score {score:.0%} on changed lines",
                            f"{(d.get('mutation') or {}).get('survived', 0)} mutant(s) survived")
                elif k == "stage_completed" and stage == "reconcile":
                    refuted = (d.get("counts") or {}).get("REFUTED", 0)
                    if refuted:
                        add(e, "claims_refuted", "warn", f"{refuted} claim(s) refuted by execution")
                elif k == "stage_completed" and stage == "guard_in":
                    flagged = [x for x in d.get("events", []) if x.get("severity") in ("warn", "block")]
                    if flagged:
                        add(e, "guard", "warn", f"Input guard flagged {len(flagged)} item(s)", ", ".join(sorted({x['guard'] for x in flagged})))
                elif k == "stage_completed" and stage == "score" and d.get("risk") is not None:
                    add(e, "risk", "info", f"Risk assessment completed: {d['risk']:.2f}")
                elif k == "stage_completed" and stage == "decide" and d.get("decision"):
                    sev = {"PASS": "ok", "CANARY": "warn", "BLOCK": "bad"}[d["decision"]]
                    add(e, "decision", sev, f"Policy decision: {d['decision']}", d.get("reason", ""))
                elif k == "run_failed":
                    add(e, "analysis_failed", "bad", "Analysis failed", e["message"])
                elif k == "run_cancelled":
                    add(e, "analysis_cancelled", "info", "Analysis cancelled")
        return sorted(items, key=lambda i: i["ts"], reverse=True)[:limit]

    # publishing a finished run ---------------------------------------------------------------------------
    def publish_info(self, run_id: str, pr_head_now: str | None = None) -> dict:
        """What publishing this run would do, and everything that currently stops it (each with the fix)."""
        meta = self.get_meta(run_id)
        report = self.get_report(run_id) if meta else None
        if meta is None:
            raise RepoError("Unknown run.", 404, "not_found")
        repo = self.registry.get(meta["repo_id"]) if meta["repo_id"] else {}
        number = (meta.get("pr") or {}).get("number")
        head = (report or {}).get("pr", {}).get("head_sha") or (meta.get("pr") or {}).get("head_sha")
        blockers: list[dict] = []

        def block(code: str, message: str, fix: str) -> None:
            blockers.append({"code": code, "message": message, "fix": fix})

        if meta["status"] != "completed" or not report:
            block("not_completed", "This analysis has no finished report.", "Wait for it to complete, or re-run it.")
        if repo.get("kind") != "github" or not number:
            block("not_github_pr", "Only pull requests of a connected GitHub repository can be published to.", "Analyse a pull request from a connected GitHub repository.")
        if not publishing_enabled():
            block("disabled", "Publishing is switched off on this server (a safety default).",
                  "Add the line SENTINEL_ALLOW_PUBLISH=1 to the .env file, then restart the server.")
        if not (os.getenv("SENTINEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")):
            block("no_token", "No GitHub token is configured on the server.", "Add GITHUB_TOKEN=<your token> to the .env file, then restart the server.")
        if report and report.get("pr", {}).get("is_fork") and not os.getenv("SENTINEL_ALLOW_PUBLISH_FORKS"):
            block("fork", "This pull request comes from a fork; publishing to forks is off.", "Add SENTINEL_ALLOW_PUBLISH_FORKS=1 to .env and restart, if you are sure.")
        if run_id in self._publishing:
            block("in_progress", "A publish for this run is already in progress.", "Wait for it to finish.")
        history = meta.get("publish_history") or []
        last_ok = next((h for h in reversed(history) if h.get("published")), None)
        return {"run_id": run_id, "allowed": not blockers, "blockers": blockers,
                "target": {"repository": repo.get("full_name"), "pr_number": number, "head_sha": head, "decision": (report or {}).get("decision")},
                "targets": self.publish_targets(repo.get("full_name") if repo.get("kind") == "github" else None),
                "will_post": {"comment": "A pull request comment with the decision, evidence and risk. If SentinelPR already commented on this PR, that comment is updated in place.",
                              "status": "A commit status on the analysed commit (success for PASS and CANARY, failure for BLOCK). It can be made a required check in branch protection.",
                              "check_run": "A Check Run, only through a GitHub App: GitHub does not let a personal access token create one."},
                "stale": (pr_head_now != head) if (pr_head_now and head) else None, "already_published": last_ok, "history": history[-5:]}

    def publish_targets(self, repository: str | None) -> dict:
        """Which of the three things can be posted, with the credential each uses and, if not available, exactly why."""
        context = self.cfg.get("publish.status_context", "SentinelPR / gate")
        check = {"available": False, "via": None, "needs": "A GitHub App with Checks: Read and write, installed on the repository", "setup": "docs/github-app.md", "problems": []}
        app = self.app_auth
        if app is None or not app.configured:
            check["problems"].append("No GitHub App is configured. GitHub only allows a GitHub App to create Check Runs; a personal access token cannot.")
        elif repository:
            info = app.describe(repository)
            check["app"] = {k: info["app"][k] for k in ("name", "slug", "html_url")} if info.get("app") else None
            check["problems"] += info["problems"]
            check["available"] = bool(info.get("app") and info.get("checks_write") and info.get("installation") and not info["problems"])
            check["via"] = "GitHub App" if check["available"] else None
        return {"comment": {"available": True, "via": "personal access token", "needs": "Pull requests: Read and write (and Issues: Read and write)"},
                "status": {"available": True, "via": "personal access token", "needs": "Commit statuses: Read and write", "context": context},
                "check_run": check}

    def publish_existing(self, run_id: str, *, force: bool = False, publish_fn=None, targets: dict | None = None) -> dict:
        info = self.publish_info(run_id)
        if info["blockers"]:
            first = info["blockers"][0]
            raise RepoError(f"{first['message']} {first['fix']}", 409 if first["code"] == "in_progress" else 403, "publish_" + first["code"])
        if info["already_published"] and not force:
            raise RepoError("This result was already published. Confirm \"publish again\" to post it once more.", 409, "already_published")
        with self._lock:
            if run_id in self._publishing:
                raise RepoError("A publish for this run is already in progress.", 409, "publish_in_progress")
            self._publishing.add(run_id)
        try:
            meta, report = self.get_meta(run_id), self.get_report(run_id)
            repo = self.registry.get(meta["repo_id"])
            log = self.get_log(run_id) or EventLog(run_id, self._path(run_id) / "events.jsonl")
            spec = SimpleNamespace(pr=SimpleNamespace(is_fork=bool(report["pr"].get("is_fork"))), head=report["pr"]["head_sha"], cfg=self.cfg)
            want = {t: bool((targets or {}).get(t, True)) for t in ("comment", "status", "check_run")}
            if not any(want.values()):
                raise RepoError("Choose at least one thing to publish.", 422, "nothing_selected")
            check_token, app_error = None, None
            if want["check_run"] and self.app_auth is not None and self.app_auth.configured:
                try:
                    check_token = self.app_auth.token(repo["full_name"], {"checks": "write"})  # one repository, checks only, one hour
                except AppAuthError as exc:
                    app_error = {"code": exc.code, "message": exc.message, "fix": exc.fix}
            outcome = publish_report(log, report, spec, meta, repo, publish_fn=publish_fn, targets=want, check_token=check_token)
            if app_error:
                outcome["check_run_app_error"] = app_error
            record = describe_publish(outcome, repo["full_name"], meta["pr"]["number"], report["pr"]["head_sha"], want)
            meta = self._read_meta(run_id) or meta
            meta["publish"] = record
            meta["publish_history"] = (meta.get("publish_history") or []) + [record]
            if self._read_meta(run_id) is not None:
                self._write_meta(meta)
            return record
        finally:
            with self._lock:
                self._publishing.discard(run_id)

    # runs made with the command line ----------------------------------------------------------------------------
    def _legacy_dirs(self) -> list[Path]:
        mine = self.cfg.workdir / "runs"
        dirs = [mine] if mine.exists() and any(mine.glob("*.json")) else []
        return dirs or [self.cfg.repo_root / "bench" / "results" / "demo" / "runs"]

    def _legacy_runs(self) -> list[dict]:
        out = []
        for directory in self._legacy_dirs():
            for path in directory.glob("run-*.json"):
                try:
                    r = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                created = datetime.fromisoformat(r["created_at"]).timestamp() if r.get("created_at") else path.stat().st_mtime
                out.append({"schema": "sentinelpr.run-meta/1", "run_id": r["run_id"], "repo_id": DEMO_ID, "repo_name": "sentinelpr/unierp-demo",
                            "pr": {k: r["pr"].get(k) for k in ("number", "title", "author", "base_sha", "head_sha")} | {"ref": str(r["pr"].get("number") or "")},
                            "status": "completed", "decision": r["decision"], "risk": r["risk"], "created_at": created, "started_at": created,
                            "ended_at": created + (r.get("elapsed_s") or 0), "elapsed_s": r.get("elapsed_s"), "tokens": r.get("tokens", 0), "error": None,
                            "options": {"llm": bool(r.get("llm", {}).get("calls"))}, "imported": True, "config": run_config(r),
                            "sentinel_version": r.get("sentinel_version")})
        return out

    def _legacy_report(self, run_id: str) -> dict | None:
        for directory in self._legacy_dirs():
            path = directory / f"{run_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return None


# --- helpers ----------------------------------------------------------------------------------------------------


def publishing_enabled() -> bool:
    return os.getenv("SENTINEL_ALLOW_PUBLISH", "").lower() in ("1", "true", "yes")


def describe_publish(outcome: dict, repo: str, number: int, head_sha: str, wanted: dict | None = None) -> dict:
    """What GitHub *confirmed*. The comment, the commit status and the Check Run are reported separately; each counts as
    created only when GitHub returned its id. 'Not attempted' (not selected, or no GitHub App) is distinct from 'refused'."""
    from sentinel.publish.diagnose import explain, token_kind

    wanted = wanted or {"comment": True, "status": True, "check_run": True}
    kind = token_kind(os.getenv("SENTINEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"))
    comment_id, status_id, check_id = outcome.get("comment"), outcome.get("status"), outcome.get("check_run")

    def failure(what: str, code, detail) -> dict:
        d = redact(detail or {"status": code, "message": ""})
        return {"error": code, "github_message": d.get("message"), "accepted_permissions": d.get("accepted_permissions"), **explain(what, d, kind)}

    comment = {"posted": bool(comment_id), "id": comment_id, "attempted": wanted.get("comment", True),
               "url": f"https://github.com/{repo}/pull/{number}#issuecomment-{comment_id}" if comment_id else None, "error": None}
    if outcome.get("comment_error") is not None:
        comment.update(failure("comment", outcome["comment_error"], outcome.get("comment_detail")))
    status = {"posted": bool(status_id), "id": status_id, "attempted": wanted.get("status", True), "error": None}
    if outcome.get("status_error") is not None:
        status.update(failure("status", outcome["status_error"], outcome.get("status_detail")))
    check = {"created": bool(check_id), "id": check_id, "attempted": bool(wanted.get("check_run", True) and outcome.get("check_run_skipped") is None and not outcome.get("check_run_app_error")),
             "error": None, "explanation": None, "fix": None}
    if outcome.get("check_run_error") is not None:
        check.update(failure("check_run", outcome["check_run_error"], outcome.get("check_run_detail")))
    elif outcome.get("check_run_app_error"):
        e = outcome["check_run_app_error"]
        check.update(error=e["code"], explanation=e["message"], fix=e["fix"])
    elif outcome.get("check_run_skipped"):
        check.update(explanation="Not attempted: creating a Check Run needs a GitHub App, and none is configured. GitHub does not let a personal access token create one.",
                     fix="Set up a GitHub App (docs/github-app.md), or use the commit status, which a personal token can post and which can be a required check.")
    elif not wanted.get("check_run", True):
        check["explanation"] = "Not selected."
    return {
        "at": time.time(), "published": bool(comment_id or status_id or check_id), "repository": repo, "pr_number": number, "head_sha": head_sha,
        "comment": comment, "status": status, "check_run": check, "reason": outcome.get("reason"), "error": outcome.get("error"),
    }


def publish_report(events: EventLog, report: dict, spec, meta: dict, repo: dict, publish_fn=None, targets: dict | None = None, check_token=None) -> dict:
    """Post the Check Run and PR comment, only if the operator enabled it. The outcome is recorded as it happened:
    GitHub rejecting a call (for example a token without checks:write) is reported, never papered over."""
    events.emit("publish", "stage_started", "running", "publishing to GitHub")
    token = os.getenv("SENTINEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    skip = None
    if not publishing_enabled():
        skip = "Publishing is disabled on this server. Set SENTINEL_ALLOW_PUBLISH=1 to allow it."
    elif repo.get("kind") != "github" or not meta["pr"].get("number"):
        skip = "Only pull requests of a connected GitHub repository can be published."
    elif not token:
        skip = "No GITHUB_TOKEN is configured on the server."
    elif spec.pr.is_fork and not os.getenv("SENTINEL_ALLOW_PUBLISH_FORKS"):
        skip = "This is a fork pull request; publishing to forks is off unless SENTINEL_ALLOW_PUBLISH_FORKS is set."
    if skip:
        events.emit("publish", "stage_skipped", "skipped", skip, {"reason": skip})
        return {"published": False, "reason": skip}
    if publish_fn is None:
        from sentinel.publish.github import publish as publish_fn  # noqa: PLC0415
    try:
        result = publish_fn(report, spec.cfg, repo["full_name"], token, head_sha=spec.head, check_token=check_token, targets=targets)
    except Exception as exc:  # noqa: BLE001 - network, auth and API errors are all reported, not raised
        message = redact(f"{type(exc).__name__}: {exc}")
        events.emit("publish", "stage_failed", "failed", f"publishing failed: {message}", {"error": message})
        return {"published": False, "error": message}
    ok = bool(result.get("check_run") or result.get("comment") or result.get("status"))
    errors = {k: v for k, v in result.items() if k.endswith("_error")}  # status codes only; GitHub's messages are kept in the stored record
    events.emit("publish", "stage_completed" if ok else "stage_failed", "completed" if ok else "failed",
                ("published: " + ", ".join(k for k in ("comment", "status", "check_run") if result.get(k))) + (f"; GitHub rejected: {errors}" if errors else ""), {"result": result})
    return {"published": ok, **result}


def redact_report(report: dict) -> dict:
    """The report is stored and served to browsers: scrub secrets from anything user-authored."""
    return redact(report)


def run_config(report: dict) -> dict:
    """The configuration that produced a decision, so two runs can be compared like for like."""
    llm = report.get("llm") or {}
    return {"sentinel_version": report.get("sentinel_version"), "risk_model": report.get("risk_model"), "calibrated": report.get("calibrated"),
            "thresholds": report.get("thresholds"), "impact_mode": report.get("impact_mode"), "prompts": report.get("prompts"),
            "retrieval": {k: (report.get("retrieval") or {}).get(k) for k in ("mode", "top_k")},
            "llm_used": bool(llm.get("calls")), "llm_providers": llm.get("by_provider"), "explanation_source": report.get("explanation_source")}


def detail_from_report(node: str, r: dict) -> dict:
    """Stage detail for runs that have no stored events (imported CLI runs), from the recorded report."""
    v = r.get("verification") or {}
    if node == "guard_in":
        return {"events": [e for e in r.get("guard_events", []) if e["stage"] == "in"][:40], "rejected": r.get("rejected")}
    if node == "parse_diff":
        return {"files": [{"path": c["path"], "status": c["status"], "added": len(c.get("added_lines", [])), "removed": len(c.get("removed_lines", []))} for c in r.get("changes", [])[:40]],
                "units": [{"id": u["id"], "kind": u["kind"], "change_type": u["change_type"], "changed_lines": len(u.get("changed_lines", []))} for u in r.get("change_units", [])[:40]]}
    if node == "impact":
        return {"mode": r.get("impact_mode"), "claims": [c for c in r.get("claims", []) if c["agent"] in ("impact", "static", "lexical")][:40], "llm_risk": r.get("llm_risk")}
    if node == "historian":
        return {"claims": [c for c in r.get("claims", []) if c["agent"] == "historian"][:40], **(r.get("history") or {})}
    if node == "signals":
        return {"intent": r.get("intent"), "features": r.get("signals")}
    if node == "select_tests":
        return r.get("selection") or {}
    if node == "verify":
        tests = {**(v.get("tests") or {}), "failed": [f["nodeid"] if isinstance(f, dict) else f for f in (v.get("tests") or {}).get("failed", [])]}
        return {"tests": tests, "changed_lines": v.get("changed_lines"), "mutation": {k: (v.get("mutation") or {}).get(k) for k in ("score", "duration_s")}, "verdicts": r.get("verdict_counts")}
    if node == "reconcile":
        return {"counts": r.get("verdict_counts")}
    if node == "score":
        return {"risk": r.get("risk"), "model": r.get("risk_model"), "calibrated": r.get("calibrated"), "contributions": r.get("contributions"), "features": r.get("features")}
    if node == "decide":
        return {"decision": r.get("decision"), "reason": r.get("decision_reason"), "thresholds": r.get("thresholds"), "explanation_source": r.get("explanation_source")}
    return {}


def reconstruct_events(report: dict) -> list[dict]:
    """Events for a run that was made before live events existed, rebuilt from its recorded trace.

    Every event is flagged ``reconstructed``: the order and timings are real (they come from the
    trace) but sub-steps such as individual mutants were not recorded.
    """
    run_id = report["run_id"]
    trace = sorted(report.get("trace", []), key=lambda t: t["start"])
    events: list[dict] = []

    def add(ts, stage, kind, status, message, data=None):
        seq = len(events) + 1
        events.append({"schema": "sentinelpr.event/1", "id": f"{run_id}:{seq}", "run_id": run_id, "seq": seq, "ts": ts, "stage": stage,
                       "kind": kind, "status": status, "message": message, "data": {**(data or {}), "reconstructed": True}})

    t0 = trace[0]["start"] if trace else 0.0
    add(t0, "run", "run_started", "running", "analysis started (reconstructed from the recorded trace)")
    boundaries = []
    for t in trace:
        boundaries.append((t["start"], 0, t))
        boundaries.append((t["end"], 1, t))
    for ts, phase, t in sorted(boundaries, key=lambda b: (b[0], b[1])):
        if phase == 0:
            add(ts, t["node"], "stage_started", "running", f"{t['node']} started")
        else:
            failed = t.get("status") == "error"
            add(ts, t["node"], "stage_failed" if failed else "stage_completed", "failed" if failed else "completed", t.get("note") or f"{t['node']} finished",
                {"duration_s": round(t["end"] - t["start"], 3), "tokens": t.get("tokens", 0), "model": t.get("model"), "retries": t.get("retries", 0),
                 "note": t.get("note"), "started_at": t["start"], "ended_at": t["end"], "detail": detail_from_report(t["node"], report)})
    end = trace[-1]["end"] if trace else t0
    executed = {t["node"] for t in trace}
    for node in sorted(PIPELINE_NODES - executed | {"canary"}):
        reason = SKIP_REASONS.get(node) or "Not reached."
        add(end, node, "stage_skipped", "skipped", reason, {"reason": reason})
    add(end, "run", "run_completed", "completed", f"analysis finished: {report['decision']}", {"decision": report["decision"], "risk": report["risk"]})
    return events


def compare_runs(a_meta: dict, a: dict, b_meta: dict, b: dict) -> dict:
    """What changed between two reports: decision, risk, evidence, tests, configuration."""
    def claims(r):
        return {(c["type"], c["target"]): c.get("status", "UNCHECKED") for c in r.get("claims", [])}

    ca, cb = claims(a), claims(b)
    failed_a = {f["nodeid"] for f in ((a.get("verification") or {}).get("tests") or {}).get("failed", [])}
    failed_b = {f["nodeid"] for f in ((b.get("verification") or {}).get("tests") or {}).get("failed", [])}
    sel_a, sel_b = set((a.get("selection") or {}).get("tests", [])), set((b.get("selection") or {}).get("tests", []))
    fa, fb = a.get("features") or {}, b.get("features") or {}
    ma = ((a.get("verification") or {}).get("mutation") or {}).get("score")
    mb = ((b.get("verification") or {}).get("mutation") or {}).get("score")
    cfg_a, cfg_b = run_config(a), run_config(b)
    return {
        "a": {"run_id": a_meta["run_id"], "decision": a["decision"], "risk": a["risk"], "head_sha": (a.get("pr") or {}).get("head_sha")},
        "b": {"run_id": b_meta["run_id"], "decision": b["decision"], "risk": b["risk"], "head_sha": (b.get("pr") or {}).get("head_sha")},
        "same_pr": a_meta["repo_id"] == b_meta["repo_id"] and a_meta["pr"].get("ref") == b_meta["pr"].get("ref"),
        "same_head": (a.get("pr") or {}).get("head_sha") == (b.get("pr") or {}).get("head_sha"),
        "decision_changed": a["decision"] != b["decision"],
        "risk_delta": None if a["risk"] is None or b["risk"] is None else round(b["risk"] - a["risk"], 4),
        "mutation_score": {"a": ma, "b": mb},
        "claims": {"added": [{"type": k[0], "target": k[1], "status": cb[k]} for k in cb if k not in ca],
                   "removed": [{"type": k[0], "target": k[1], "status": ca[k]} for k in ca if k not in cb],
                   "status_changed": [{"type": k[0], "target": k[1], "from": ca[k], "to": cb[k]} for k in ca if k in cb and ca[k] != cb[k]]},
        "tests": {"newly_failing": sorted(failed_b - failed_a), "fixed": sorted(failed_a - failed_b),
                  "selected_added": sorted(sel_b - sel_a), "selected_removed": sorted(sel_a - sel_b)},
        "features": [{"feature": k, "a": fa.get(k), "b": fb.get(k)} for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)],
        "config": [{"key": k, "a": cfg_a.get(k), "b": cfg_b.get(k)} for k in cfg_a if cfg_a.get(k) != cfg_b.get(k)],
    }
