"""End-to-end validation against a *running* SentinelPR server.

    python -m sentinel.validate                                   # local demo, sandbox, deployments
    python -m sentinel.validate --public-pr more-itertools/more-itertools#1338
    python -m sentinel.validate --out docs/validation-latest.md

Each stage is reported as PASSED, FAILED or NOT TESTED (with the exact reason, usually a missing
credential or service). A stage is never marked passed because something *would* work: it must have
been observed, and the run or deployment id that shows it is recorded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

PASSED, FAILED, NOT_TESTED = "PASSED", "FAILED", "NOT TESTED"


@dataclass
class Result:
    stage: str
    status: str
    what: str
    evidence: str = ""
    detail: list[str] = field(default_factory=list)


class Client:
    def __init__(self, base: str, token: str | None):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"} if token else {})

    def call(self, method: str, path: str, **kw):
        response = self.http.request(method, f"{self.base}/api{path}", **kw)
        data = response.json() if response.content else None
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {(data or {}).get('error', {}).get('message', response.text[:200])}")
        return data

    def wait_run(self, run_id: str, timeout: float = 900) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            meta = self.call("GET", f"/analyses/{run_id}")["meta"]
            if meta["status"] not in ("queued", "running"):
                return self.call("GET", f"/analyses/{run_id}")
            time.sleep(3)
        raise RuntimeError(f"run {run_id} did not finish within {timeout:.0f}s")

    def wait_deployment(self, dep_id: str, timeout: float = 600) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            data = self.call("GET", f"/deployments/{dep_id}")
            if data["state"]["status"] not in ("queued", "provisioning", "rolling_out", "paused"):
                return data
            time.sleep(3)
        raise RuntimeError(f"deployment {dep_id} did not finish within {timeout:.0f}s")


def check_status(c: Client) -> tuple[Result, dict]:
    status = c.call("GET", "/service/status")
    checks = {x["id"]: x for x in status["checks"]}
    detail = [f"{k}: {'ok' if v['ok'] else 'NOT available'} — {v['detail']}" for k, v in checks.items()]
    return Result("Environment", PASSED, "server reachable; capabilities reported", f"version {status['version']}", detail), checks


def demo_run(c: Client) -> Result:
    pulls = c.call("GET", "/repos/unierp-demo/pulls")
    if not pulls:
        return Result("Local demo repository", NOT_TESTED, "analyse a demo pull request", "no demo branches (run python demo/scenarios.py)")
    branch = next((p["head_ref"] for p in pulls if "hidden" in p["head_ref"]), pulls[0]["head_ref"])
    run = c.call("POST", "/analyses", json={"repo_id": "unierp-demo", "ref": branch, "llm": False})
    done = c.wait_run(run["run_id"])
    report = done["report"]
    ok = done["meta"]["status"] == "completed" and report and report["verification"]
    events = c.call("GET", f"/analyses/{run['run_id']}/events")["events"]
    consistent = events[-1]["kind"] == "run_completed" and events[-1]["data"]["decision"] == report["decision"]
    return Result("Local demo repository", PASSED if ok and consistent else FAILED, f"analyse the demo PR {branch} end to end, host execution",
                  f"run {run['run_id']}: {report['decision']} (risk {report['risk']}), {len(events)} events, stream and report agree: {consistent}")


def public_pr(c: Client, spec: str, checks: dict) -> Result:
    what = f"analyse a real public pull request ({spec})"
    if not spec:
        return Result("Public GitHub PR", NOT_TESTED, what, "no --public-pr given")
    repo_part, _, number = spec.partition("#")
    try:
        repo = c.call("POST", "/repos", json={"url": repo_part})
    except RuntimeError as exc:
        return Result("Public GitHub PR", FAILED, what, str(exc))
    run = c.call("POST", "/analyses", json={"repo_id": repo["id"], "ref": number, "llm": True})
    done = c.wait_run(run["run_id"], 1500)
    meta, report = done["meta"], done["report"]
    if meta["status"] != "completed":
        return Result("Public GitHub PR", FAILED, what, f"run {run['run_id']} {meta['status']}: {meta.get('error')}")
    v = report["verification"]
    sandboxed = meta.get("execution") == "docker"
    return Result("Public GitHub PR", PASSED, what + (" in the Docker sandbox" if sandboxed else " ON THE HOST"),
                  f"run {run['run_id']}: head {report['pr']['head_sha'][:10]}, {v['tests']['passed']} tests passed / {len(v['tests']['failed'])} failed, "
                  f"decision {report['decision']} (risk {report['risk']}), execution={meta.get('execution')}",
                  [] if sandboxed else ["WARNING: Docker was unavailable, so this ran unsandboxed."])


def llm_stage(c: Client) -> Result:
    status = c.call("GET", "/llm/status")
    configured = [n for n, p in status["providers"].items() if p["configured"]]
    if not configured:
        return Result("Live LLM provider", NOT_TESTED, "real provider call", "no provider configured: set GROQ_API_KEY or GEMINI_API_KEY (or run Ollama) and restart the server")
    results = [c.call("POST", "/llm/check", json={"provider": n, "probe": True}) for n in configured]
    ok = [r for r in results if r["ok"]]
    return Result("Live LLM provider", PASSED if ok else FAILED, "connectivity, model availability and a one-token completion",
                  "; ".join(f"{r['provider']}: {r['state']}" + (f", probe {r['probe']['latency_ms']} ms" if r.get("probe") else "") for r in results))


def deployments(c: Client) -> list[Result]:
    targets = {t["name"]: t for t in c.call("GET", "/deployments/targets")}
    out: list[Result] = []
    plan = [("Staging canary: healthy rollout", "local-processes", None, "promoted"),
            ("Staging canary: SLO breach and rollback", "local-processes", {"kind": "errors", "rate": 0.5}, "rolled_back")]
    if targets.get("docker-staging", {}).get("ready"):
        plan.append(("Staging canary on Docker containers: SLO breach and rollback", "docker-staging", {"kind": "slow", "rate": 0.5, "delay_ms": 500}, "rolled_back"))
    else:
        out.append(Result("Staging canary on Docker containers", NOT_TESTED, "rollout and rollback on real containers", targets.get("docker-staging", {}).get("note") or "Docker target not ready"))
    for name, target, fault, expect in plan:
        try:
            dep = c.call("POST", "/deployments", json={"target": target, "stable_ref": "main", "candidate_ref": c.call("GET", "/repos/unierp-demo/pulls")[0]["head_ref"],
                                                       "step_seconds": 6, "confirm": True, "test_fault": fault})
            data = c.wait_deployment(dep["id"])
        except RuntimeError as exc:
            out.append(Result(name, FAILED, f"{target}", str(exc)))
            continue
        s = data["state"]
        proven = (s["promotion_verified"] is True) if expect == "promoted" else (s["rollback_verified"] is True)
        out.append(Result(name, PASSED if s["status"] == expect and proven else FAILED, f"target {target}" + (f", injected {fault['kind']} fault (validation only)" if fault else ""),
                          f"deployment {dep['id']}: {s['status']}, {'promotion' if expect == 'promoted' else 'rollback'} verified={proven}, "
                          f"exposed {s['exposed_share']}, " + (f"breach detected after {s['time_to_detection_s']}s" if s["time_to_detection_s"] is not None else "no breach"), [f"{e['kind']}: {e['message'][:110]}" for e in data['audit'] if e['kind'] in ('step_result', 'rollback_check', 'promotion_check')]))
    return out


def publishing_stage(checks: dict) -> Result:
    p = checks.get("publish")
    if not p or not p["ok"]:
        return Result("GitHub Check Run / PR comment", NOT_TESTED, "publish to a pull request in a repository you own",
                      "disabled or no token: set SENTINEL_ALLOW_PUBLISH=1 and GITHUB_TOKEN, then analyse a PR in your own repository with 'Publish result to GitHub' ticked")
    return Result("GitHub Check Run / PR comment", NOT_TESTED, "publish to a pull request in a repository you own",
                  "publishing is enabled but this script never writes to GitHub on its own: tick 'Publish result to GitHub' on a PR in a repository you own")


def render(results: list[Result], base: str) -> str:
    counts = {s: sum(r.status == s for r in results) for s in (PASSED, FAILED, NOT_TESTED)}
    lines = [f"# SentinelPR validation — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
             f"Server: {base}. **{counts[PASSED]} passed, {counts[FAILED]} failed, {counts[NOT_TESTED]} not tested.**", "",
             "| Stage | Result | What was tested | Evidence |", "|---|---|---|---|"]
    lines += [f"| {r.stage} | **{r.status}** | {r.what} | {r.evidence} |" for r in results]
    for r in results:
        if r.detail:
            lines += ["", f"### {r.stage}", *[f"- {d}" for d in r.detail]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.getenv("SENTINEL_API", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.getenv("SENTINEL_API_TOKEN"))
    parser.add_argument("--public-pr", default="", help="owner/repo#number of a public pull request to analyse")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    c = Client(args.base, args.token)
    results: list[Result] = []
    try:
        res, checks = check_status(c)
        results.append(res)
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"cannot reach the server at {args.base}: {exc}", file=sys.stderr)
        return 2
    steps = [lambda: demo_run(c), lambda: public_pr(c, args.public_pr, checks), lambda: llm_stage(c)]
    for step in steps:
        try:
            results.append(step())
        except Exception as exc:  # noqa: BLE001 - a crashed stage is a failed stage
            results.append(Result("(stage crashed)", FAILED, step.__name__, f"{type(exc).__name__}: {exc}"))
    if not args.no_deploy:
        try:
            results += deployments(c)
        except Exception as exc:  # noqa: BLE001
            results.append(Result("Staging canary", FAILED, "deployments", f"{type(exc).__name__}: {exc}"))
    results.append(publishing_stage(checks))
    report = render(results, args.base)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(report)
    return 1 if any(r.status == FAILED for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
