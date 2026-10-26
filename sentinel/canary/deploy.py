"""Staged rollouts of a candidate commit, with proof instead of assumptions.

A deployment is only "promoted" or "rolled back" after measurement says so:

* the requested weight is read back from the router;
* the share of traffic the candidate actually served is computed from the *applications'* own request
  counters, and must match the weight;
* the candidate's error rate and p95 are judged against the SLO on each step's own traffic;
* a rollback is confirmed by sending probe requests and checking, from the version header the
  application itself puts on every response, that none reached the candidate.

State and an audit trail persist under ``<workdir>/service/deployments/<id>/``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sentinel.canary.monitor import SLO, Snapshot, evaluate, reading
from sentinel.canary.targets import DeployError, Router, TargetConfig, build_target, capabilities, probe, resolve_ref
from sentinel.canary.traffic import run_traffic
from sentinel.service.atomic import read_json, write_json
from sentinel.service.events import EventLog, redact

log = logging.getLogger("sentinel.deploy")
ACTIVE = ("queued", "provisioning", "rolling_out", "paused")
STEPS = (0.10, 0.25, 0.50, 1.00)
CHUNK_S = 3.0
MAX_EXTENSIONS = 3


class Stop(Exception):
    """The operator aborted the rollout."""


@dataclass
class Control:
    paused: threading.Event = field(default_factory=threading.Event)
    aborted: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)

    def checkpoint(self, on_pause=None, on_resume=None) -> None:
        if self.aborted.is_set():
            raise Stop()
        if self.paused.is_set():
            if on_pause:
                on_pause()
            while self.paused.is_set() and not self.aborted.is_set():
                self.resume.wait(0.5)
            if self.aborted.is_set():
                raise Stop()
            if on_resume:
                on_resume()


def new_id() -> str:
    return f"dep-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def tolerance(weight: float, n: int) -> float:
    """How far the observed candidate share may sit from the requested weight (binomial noise plus a floor)."""
    return max(0.05, 3 * math.sqrt(max(weight * (1 - weight), 1e-9) / max(n, 1)))


class DeploymentManager:
    def __init__(self, service_cfg, app_repo: Path, targets: dict[str, TargetConfig], root: Path, app_dir: str = "."):
        self.app_repo, self.app_dir, self.targets = Path(app_repo), app_dir, targets
        self.dir = Path(root) / "deployments"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-deploy")  # one rollout at a time
        self._controls: dict[str, Control] = {}
        self._logs: dict[str, EventLog] = {}
        self._lock = threading.RLock()
        self._recover()

    # storage ---------------------------------------------------------------------------------------------
    def _path(self, dep_id: str) -> Path:
        if not dep_id.replace("-", "").isalnum():
            raise DeployError("Unknown deployment.", "not_found", 404)
        return self.dir / dep_id

    def _save(self, state: dict) -> None:
        write_json(self._path(state["id"]) / "state.json", state)

    def get(self, dep_id: str) -> dict:
        path = self._path(dep_id) / "state.json"
        if not path.exists():
            raise DeployError("Unknown deployment.", "not_found", 404)
        return read_json(path)

    def list(self) -> list[dict]:
        out = []
        for path in self.dir.glob("*/state.json"):
            try:
                state = read_json(path)
            except (json.JSONDecodeError, PermissionError):
                continue
            out.append({k: state.get(k) for k in ("id", "status", "phase", "target", "environment", "stable", "candidate", "created_at", "ended_at", "outcome", "test_fault", "reason")})
        return sorted(out, key=lambda d: d["created_at"] or 0, reverse=True)

    def audit(self, dep_id: str) -> list[dict]:
        log_ = self._logs.get(dep_id)
        if log_ is not None:
            return log_.since(0)
        return EventLog.load(dep_id, self._path(dep_id) / "audit.jsonl").since(0)

    def _recover(self) -> None:
        for path in self.dir.glob("*/state.json"):
            state = read_json(path)
            if state["status"] in ACTIVE:
                state.update(status="failed", reason="Interrupted: the SentinelPR server stopped during this deployment. Its staging resources were disposable "
                                                     "and are removed on the next start; verify the environment before trusting it.", ended_at=time.time(), outcome="failed")
                self._save(state)
                EventLog.load(state["id"], path.parent / "audit.jsonl").emit("deployment", "interrupted", "failed", state["reason"])

    # creating ---------------------------------------------------------------------------------------------
    def create(self, *, target: str, stable_ref: str, candidate_ref: str, slo: dict | None = None, step_seconds: float = 12.0, steps=STEPS,
               test_fault: dict | None = None, confirm: bool = False, run_id: str | None = None) -> dict:
        if not confirm:
            raise DeployError("A deployment must be confirmed explicitly (confirm: true).", "confirmation_required", 400)
        config = self.targets.get(target)
        if config is None:
            raise DeployError(f"Unknown deployment target {target!r}. Configured: {sorted(self.targets)}.", "unknown_target", 404)
        config.validate()
        caps = capabilities(config)
        if not caps["weighted_routing"]:
            raise DeployError(caps["note"], "no_weighted_routing", 409)
        if any(d["status"] in ACTIVE for d in self.list()):
            raise DeployError("Another deployment is in progress; only one rollout runs at a time.", "busy", 409)
        stable_sha, candidate_sha = resolve_ref(self.app_repo, stable_ref), resolve_ref(self.app_repo, candidate_ref)
        if stable_sha == candidate_sha:
            raise DeployError("The candidate and the baseline are the same commit; there is nothing to roll out.", "same_commit", 422)
        fault_env = {}
        if test_fault and test_fault.get("kind", "none") != "none":
            if config.environment not in ("local", "staging"):
                raise DeployError("Test faults are only allowed in local or staging targets.", "fault_refused", 403)
            fault_env = {"UNIERP_FAULT": str(test_fault["kind"]), "UNIERP_FAULT_RATE": str(test_fault.get("rate", 0.3)), "UNIERP_FAULT_DELAY_MS": str(test_fault.get("delay_ms", 400))}
        dep_id = new_id()
        state = {
            "schema": "sentinelpr.deployment/1", "id": dep_id, "status": "queued", "phase": "queued", "outcome": None, "reason": None,
            "target": target, "kind": config.kind, "environment": config.environment, "capabilities": caps,
            "stable": {"ref": stable_ref, "sha": stable_sha}, "candidate": {"ref": candidate_ref, "sha": candidate_sha}, "analysis_run": run_id,
            "slo": asdict(SLO(**(slo or {}))), "steps": list(steps), "step_seconds": step_seconds,
            "test_fault": ({"injected_for_validation": True, **test_fault} if fault_env else None),
            "current_step": None, "weight_requested": 0.0, "weight_observed": None, "measurements": [], "step_results": [],
            "candidate_requests": 0, "total_requests": 0, "exposed_share": None, "rollback_verified": None, "promotion_verified": None, "time_to_detection_s": None,
            "created_at": time.time(), "started_at": None, "ended_at": None,
        }
        self._save(state)
        self._logs[dep_id] = EventLog(dep_id, self._path(dep_id) / "audit.jsonl")
        self._controls[dep_id] = Control()
        self._logs[dep_id].emit("deployment", "created", "queued", f"deployment of {candidate_sha[:10]} against baseline {stable_sha[:10]} on {target} ({config.environment})",
                                {"actor": "operator", "target": target, "test_fault": state["test_fault"]})
        self.pool.submit(self._run, dep_id, fault_env)
        return state

    # operator controls -------------------------------------------------------------------------------------
    def control(self, dep_id: str, action: str) -> dict:
        state = self.get(dep_id)
        control = self._controls.get(dep_id)
        if state["status"] not in ACTIVE or control is None:
            raise DeployError(f"The deployment is {state['status']}; it can no longer be controlled.", "not_active", 409)
        log_ = self._logs[dep_id]
        if action == "pause":
            control.resume.clear()
            control.paused.set()
        elif action == "resume":
            control.paused.clear()
            control.resume.set()
        elif action in ("abort", "rollback"):
            control.aborted.set()
            control.resume.set()
        else:
            raise DeployError("Unknown action.", "invalid_action", 422)
        log_.emit("deployment", "operator_action", "info", f"operator requested {action}", {"actor": "operator", "action": action})
        return self.get(dep_id)

    # the rollout ----------------------------------------------------------------------------------------------
    def _run(self, dep_id: str, fault_env: dict) -> None:
        state, log_, control = self.get(dep_id), self._logs[dep_id], self._controls[dep_id]
        config = self.targets[state["target"]]

        def update(**fields) -> None:
            state.update(fields)
            self._save(state)

        def say(kind: str, status: str, message: str, data: dict | None = None) -> None:
            log_.emit("deployment", kind, status, message, {"actor": "controller", **(data or {})})

        started = time.time()
        update(status="provisioning", phase="provisioning", started_at=started)
        try:
            target = build_target(config, self.app_repo, self.app_dir)
            with target.provision(state["stable"]["sha"], state["candidate"]["sha"], fault_env, lambda phase, msg: (say("provision", "running", msg), update(phase=phase))) as ep:
                router = Router(ep.router_url, os.getenv(config.options.get("router_token_env", "")) or None)
                control.checkpoint()
                self._baseline_checks(ep, say)
                update(status="rolling_out", phase="rolling_out", endpoints={"router": ep.router_url, "stable": ep.stable_url, "candidate": ep.candidate_url, **ep.notes})
                self._rollout(state, ep, router, control, update, say, started)
        except Stop:  # aborted before any traffic was shifted (while provisioning or checking health)
            say("aborted", "info", "the operator aborted the deployment before any traffic was shifted")
            update(status="rolled_back", phase="finished", outcome="aborted", rollback_verified=True, ended_at=time.time(),
                   reason="Aborted by the operator before the router sent any traffic to the candidate.")
        except DeployError as exc:
            say("failed", "failed", str(exc), {"code": exc.code})
            update(status="failed", phase="finished", outcome="failed", reason=str(exc), ended_at=time.time())
        except Exception as exc:  # noqa: BLE001
            log.error("deployment %s failed:\n%s", dep_id, traceback.format_exc())
            say("failed", "failed", f"{type(exc).__name__}: {exc}")
            update(status="failed", phase="finished", outcome="failed", reason=redact(f"{type(exc).__name__}: {exc}"), ended_at=time.time())
        finally:
            log_.close()

    def _baseline_checks(self, ep, say) -> None:
        for role, url, expected in (("baseline", ep.stable_url, ep.stable_version), ("candidate", ep.candidate_url, ep.candidate_version)):
            import httpx

            if httpx.get(f"{url}/health", timeout=10).status_code != 200:
                raise DeployError(f"The {role} is not healthy before any traffic is shifted.", "unhealthy")
            version = ""
            for _ in range(8):  # /health is not instrumented; ask a normal endpoint, tolerating a faulty one (the version header is on every answer)
                version = httpx.get(f"{url}/courses", timeout=10).headers.get("X-UniERP-Version", "")
                if version:
                    break
            if expected and version != expected:
                raise DeployError(f"The {role} reports version {version!r}, expected {expected!r}: the wrong build is running.", "wrong_version")
            say("health_check", "completed", f"{role} healthy, reports version {version or 'n/a'}", {"role": role, "version": version})

    def _rollout(self, state, ep, router: Router, control: Control, update, say, started: float) -> None:
        try:
            self._steps(state, ep, router, control, update, say, started)
        except Stop:
            say("aborted", "info", "the operator aborted the rollout; restoring the baseline")
            self._rollback(state, ep, router, update, say, started, "aborted by the operator", failed=False, outcome="aborted")

    def _steps(self, state, ep, router: Router, control: Control, update, say, started: float) -> None:
        slo = SLO(**state["slo"])
        cand_total = total = 0
        for weight in state["steps"]:
            control.checkpoint(lambda: (update(status="paused"), say("paused", "info", "rollout paused at the current weight")),
                               lambda: (update(status="rolling_out"), say("resumed", "info", "rollout resumed")))
            router.set_weight(weight)
            observed = router.get_weight()
            update(current_step=weight, weight_requested=weight, weight_observed=observed)
            say("weight_set", "completed" if abs(observed - weight) < 1e-9 else "failed", f"router weight requested {weight:.0%}, read back {observed:.0%}",
                {"requested": weight, "observed": observed})
            if abs(observed - weight) > 1e-9:
                self._rollback(state, ep, router, update, say, started, f"the router did not apply the requested weight (asked {weight}, read back {observed})", failed=True)
                return
            before_s, before_c = Snapshot.fetch(ep.stable_url), Snapshot.fetch(ep.candidate_url)
            step_started = time.time()
            while True:
                control.checkpoint(lambda: (update(status="paused"), say("paused", "info", "rollout paused")), lambda: (update(status="rolling_out"), say("resumed", "info", "resumed")))
                chunk = min(CHUNK_S, max(state["step_seconds"] - (time.time() - step_started), 1.0))
                run_traffic(ep.router_url, chunk)
                after_s, after_c = Snapshot.fetch(ep.stable_url), Snapshot.fetch(ep.candidate_url)
                s, c = reading(before_s, after_s), reading(before_c, after_c)
                n = s.requests + c.requests
                share = c.requests / n if n else 0.0
                measure = {"t": round(time.time() - started, 1), "weight_requested": weight, "weight_observed": router.get_weight(), "candidate_share": round(share, 4),
                           "stable": asdict(s), "candidate": asdict(c), "source": "application counters"}
                state["measurements"] = (state["measurements"] + [measure])[-200:]
                update(measurements=state["measurements"], candidate_requests=cand_total + c.requests, total_requests=total + n)
                verdict = evaluate(s, c, slo)
                elapsed = time.time() - step_started
                if not verdict.inconclusive and not verdict.healthy:
                    break  # a breach on enough traffic is acted on immediately, not at the end of the window
                if elapsed >= state["step_seconds"] and (not verdict.inconclusive or elapsed >= state["step_seconds"] * (1 + MAX_EXTENSIONS)):
                    break
            cand_total += c.requests
            total += n
            routing_ok = c.requests > 0 and abs(share - weight) <= tolerance(weight, n)
            result = {"weight": weight, "healthy": verdict.healthy and routing_ok, "inconclusive": verdict.inconclusive, "breaches": list(verdict.breaches),
                      "routing_verified": routing_ok, "candidate_share": round(share, 4), "stable": asdict(s), "candidate": asdict(c)}
            state["step_results"].append(result)
            update(step_results=state["step_results"], candidate_requests=cand_total, total_requests=total, exposed_share=round(cand_total / total, 4) if total else 0.0)
            say("step_result", "completed" if result["healthy"] else "failed",
                f"step {weight:.0%}: candidate served {share:.1%} of {n} requests ({c.requests}); " + ("healthy" if result["healthy"] else "; ".join(result["breaches"] or ["routing did not match the weight"])), result)
            if not routing_ok:
                self._rollback(state, ep, router, update, say, started,
                               f"traffic did not reach the candidate as configured: it served {share:.1%} at a {weight:.0%} weight ({c.requests} requests)", failed=True)
                return
            if not verdict.healthy:
                self._rollback(state, ep, router, update, say, started, "SLO breach: " + "; ".join(verdict.breaches), failed=False)
                return
        # promoted only if the candidate demonstrably serves everything now
        seen = probe(ep.router_url, 40)
        served = seen.get(ep.candidate_version, 0) if ep.candidate_version else sum(seen.values())
        verified = served == sum(seen.values()) and sum(seen.values()) > 0
        say("promotion_check", "completed" if verified else "failed", f"probe at 100%: {seen}", {"probe": seen})
        update(status="promoted" if verified else "failed", phase="finished", outcome="promoted" if verified else "failed", promotion_verified=verified, ended_at=time.time(),
               reason=("Candidate served 100% of traffic through every SLO window in this staging environment." if verified
                       else "The weight reached 100% but the probe shows other versions still answering."))

    def _rollback(self, state, ep, router: Router, update, say, started, reason: str, failed: bool, outcome: str = "rolled_back") -> None:
        detected = round(time.time() - started, 2)
        say("rollback", "running", f"rolling back: {reason}", {"reason": reason})
        router.set_weight(0.0)
        observed = router.get_weight()
        seen = probe(ep.router_url, 40)
        to_candidate = seen.get(ep.candidate_version, 0) if ep.candidate_version else 0
        to_stable = seen.get(ep.stable_version, 0) if ep.stable_version else sum(seen.values())
        verified = observed == 0.0 and to_candidate == 0 and to_stable > 0
        say("rollback_check", "completed" if verified else "failed", f"after rollback the router weight reads {observed:.0%}; of {sum(seen.values())} probe requests, "
            f"{to_candidate} reached the candidate and {to_stable} the baseline", {"probe": seen})
        update(status="rolled_back" if verified else "failed", phase="finished", outcome=(outcome if not failed else "failed"), weight_observed=observed, weight_requested=0.0,
               rollback_verified=verified, time_to_detection_s=detected, ended_at=time.time(),
               reason=reason + ("" if verified else " ROLLBACK COULD NOT BE VERIFIED: the candidate may still be receiving traffic."))
