"""HTTP API of the product: repositories, pull requests, analyses, live events, model and health.

Security model (single-user, local-first):

* Mutating requests and event streams are checked against ``SENTINEL_API_TOKEN`` when it is set.
* Browser requests must come from an allowed origin (``SENTINEL_CORS_ORIGINS``, default: the
  local dashboard), which also blocks cross-site requests to a server on localhost.
* There are no user accounts. The UI says so instead of pretending otherwise.
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import shutil
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from sentinel import __version__
from sentinel.config import Config
from sentinel.orchestrator.observe import EDGES, STAGES
from sentinel.canary.deploy import DeploymentManager
from sentinel.publish.app_auth import GitHubAppAuth
from sentinel.canary.targets import DeployError, TargetConfig, capabilities
from sentinel.service.github import GitHubClient, GitHubError
from sentinel.service.repos import RepoError, RepoRegistry
from sentinel.service.runs import RunManager, compare_runs, publishing_enabled

DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"]
STARTED = time.time()


def allowed_origins() -> list[str]:
    raw = os.getenv("SENTINEL_CORS_ORIGINS")
    return [o.strip() for o in raw.split(",") if o.strip()] if raw else DEFAULT_ORIGINS


class ConnectRequest(BaseModel):
    url: str = Field(min_length=3, max_length=300)


class AnalyzeRequest(BaseModel):
    repo_id: str = Field(min_length=1, max_length=130)
    ref: str = Field(min_length=1, max_length=200, description="PR number (GitHub) or branch name (demo)")
    llm: bool = True
    publish: bool = False  # post the Check Run / PR comment afterwards (needs SENTINEL_ALLOW_PUBLISH=1 on the server)


class LlmCheck(BaseModel):
    provider: str = Field(pattern="^(groq|gemini|ollama)$")
    probe: bool = False  # also send a one-token completion (spends a few tokens)


class PublishRequest(BaseModel):
    force: bool = False  # post again although this result was already published
    targets: dict[str, bool] | None = None  # which of comment / status / check_run to post (default: all that are available)


class TrustRequest(BaseModel):
    trusted: bool


def deploy_targets(cfg: Config) -> dict[str, TargetConfig]:
    """Explicitly configured deployment targets. The two built-ins are disposable and local; anything else comes from
    ``deploy.targets`` in sentinel.yaml. Nothing is deployable until it is listed here."""
    targets = {"local-processes": TargetConfig("local-processes", "local", "local"), "docker-staging": TargetConfig("docker-staging", "docker", "staging")}
    for name, raw in (cfg.get("deploy.targets", {}) or {}).items():
        targets[name] = TargetConfig(name, raw.get("kind", "external"), raw.get("environment", "staging"), {k: v for k, v in raw.items() if k not in ("kind", "environment")})
    return targets


class DeployRequest(BaseModel):
    target: str = Field(min_length=1, max_length=80)
    stable_ref: str = Field(default="main", max_length=200)
    candidate_ref: str = Field(min_length=1, max_length=200)
    step_seconds: float = Field(default=12.0, ge=3, le=300)
    max_error_rate: float = Field(default=0.01, ge=0, le=1)
    p95_ratio: float = Field(default=1.5, ge=1, le=20)
    test_fault: dict | None = None
    confirm: bool = False
    run_id: str | None = None


class Service:
    """The objects behind the routes, created once per API instance."""

    PULL_TTL_S = 60

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.dir = cfg.workdir / "service"
        self.registry = RepoRegistry(cfg, self.dir)
        self.runs = RunManager(cfg, self.registry, self.dir)
        self.github = GitHubClient()
        self.app_auth = GitHubAppAuth()
        self.runs.app_auth = self.app_auth
        self.deployments = DeploymentManager(cfg, self.registry.demo_path, deploy_targets(cfg), self.dir)
        self._pulls: dict[str, tuple[float, list[dict]]] = {}

    def pulls_for(self, repo_id: str, refresh: bool = False) -> list[dict]:
        """Open PRs of a repository with the latest run of each attached (GitHub results cached briefly)."""
        cached = self._pulls.get(repo_id)
        if cached and not refresh and time.time() - cached[0] < self.PULL_TTL_S and self.registry.get(repo_id)["kind"] == "github":
            items = [dict(p) for p in cached[1]]
        else:
            items = self.registry.pulls(repo_id, self.github)
            self._pulls[repo_id] = (time.time(), [dict(p) for p in items])
        latest: dict[str, dict] = {}
        for run in self.runs.list_runs(repo_id=repo_id):  # newest first
            latest.setdefault(str(run["pr"].get("ref")), run)
        for item in items:
            run = latest.get(str(item.get("number") if item.get("number") is not None else item.get("ref")))
            item["repo_id"] = repo_id
            item["latest_run"] = ({k: run[k] for k in ("run_id", "status", "decision", "risk", "created_at")} | {"head_sha": run["pr"].get("head_sha")}) if run else None
            item["stale"] = bool(run and run["pr"].get("head_sha") and item.get("head_sha") and run["pr"]["head_sha"] != item["head_sha"])
        return items


def sandbox_check() -> dict:
    from sentinel.verify import sandbox as sbx

    docker = sbx.docker_status()
    if docker["available"]:
        return {"id": "sandbox", "ok": True, "label": "Execution sandbox",
                "detail": f"{docker['detail']}. Repository tests and mutants run in containers with no network, a read-only root filesystem, "
                          "dropped capabilities and CPU, memory, process and time limits."}
    return {"id": "sandbox", "ok": False, "label": "Execution sandbox",
            "detail": f"{docker['detail']} Without it a GitHub repository can only be analysed after you explicitly trust it to run on this machine, unsandboxed. "
                      "SentinelPR never falls back to the host silently."}


def create_router(svc: Service) -> APIRouter:
    def secure(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins():
            raise HTTPException(403, {"code": "origin_not_allowed", "message": f"Origin {origin} may not call this API."})
        expected = os.getenv("SENTINEL_API_TOKEN")
        if expected:
            auth = request.headers.get("authorization", "")
            supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise HTTPException(401, {"code": "unauthorized", "message": "A valid SentinelPR API token is required."})

    router = APIRouter(prefix="/api", dependencies=[Depends(secure)])

    # --- service -------------------------------------------------------------------
    @router.get("/service/pipeline")
    def pipeline():
        return {"stages": STAGES, "edges": [{"from": a, "to": b} for a, b in EDGES]}

    @router.get("/service/status")
    def status():
        from sentinel.llm.providers import build_providers

        providers = build_providers(svc.cfg)
        runs = svc.runs.list_runs(limit=500)
        disk = shutil.disk_usage(svc.cfg.workdir if svc.cfg.workdir.exists() else svc.cfg.repo_root)
        token_set = bool(os.getenv("SENTINEL_API_TOKEN"))
        checks = [
            {"id": "git", "ok": shutil.which("git") is not None, "label": "git", "detail": shutil.which("git") or "not found on PATH"},
            {"id": "github", "ok": svc.github.authenticated, "label": "GitHub access",
             "detail": "GITHUB_TOKEN configured (private repositories readable)" if svc.github.authenticated
             else "No GITHUB_TOKEN: public repositories only, 60 API requests per hour"},
            {"id": "llm", "ok": any(p.available for p in providers.values()), "label": "LLM providers",
             "detail": ", ".join(f"{n}: {'available' if p.available else 'not configured'}" for n, p in providers.items())
             + ". Without a provider the agents use evidence-only claims; decisions never depend on LLM text."},
            {"id": "auth", "ok": token_set, "label": "API authentication",
             "detail": "Bearer token required (SENTINEL_API_TOKEN)" if token_set else "Open local single-user mode: no accounts, no token. Bind to localhost only."},
            sandbox_check(),
            {"id": "publish", "ok": publishing_enabled() and svc.github.authenticated, "label": "Publishing to GitHub",
             "detail": ("Enabled: a run may post a PR comment and a commit status with the personal token (Pull requests and Commit statuses: Read and write), "
                        "and a Check Run only through a configured GitHub App." if publishing_enabled()
                        else "Disabled (default). Set SENTINEL_ALLOW_PUBLISH=1 and a token with pull-request write access to allow runs to publish.")},
        ]
        return {"version": __version__, "python": platform.python_version(), "platform": platform.system(), "uptime_s": round(time.time() - STARTED),
                "checks": checks, "auth": "token" if token_set else "none", "publish_enabled": publishing_enabled(),
                "github": {"authenticated": svc.github.authenticated},
                "llm": {n: {"available": p.available, "model": p.model} for n, p in providers.items()},
                "queue": {"queued": sum(r["status"] == "queued" for r in runs), "running": sum(r["status"] == "running" for r in runs),
                          "max_concurrent": svc.runs.pool._max_workers},
                "storage": {"workdir": str(svc.cfg.workdir), "free_gb": round(disk.free / 1e9, 1)},
                "runs": len(runs), "repositories": len(svc.registry.list())}

    # --- repositories ------------------------------------------------------------------
    @router.get("/repos")
    def repos():
        return svc.registry.list()

    @router.post("/repos/validate")
    def validate_repo(body: ConnectRequest):
        return svc.registry.validate(body.url, svc.github)

    @router.post("/repos", status_code=201)
    def connect_repo(body: ConnectRequest):
        return svc.registry.connect(body.url, svc.github)

    @router.get("/repos/{repo_id}")
    def repo(repo_id: str):
        record = svc.registry.get(repo_id)
        return {**record, "runs": len(svc.runs.list_runs(repo_id=repo_id))}

    @router.delete("/repos/{repo_id}", status_code=204)
    def remove_repo(repo_id: str):
        svc.registry.remove(repo_id)

    @router.post("/repos/{repo_id}/trust")
    def trust(repo_id: str, body: TrustRequest):
        return svc.registry.set_trusted(repo_id, body.trusted)

    @router.post("/repos/{repo_id}/seed", status_code=202)
    def seed(repo_id: str):
        if repo_id != "unierp-demo":
            raise RepoError("Only the bundled demo repository can be seeded.", 400, "not_applicable")
        svc.registry.seed_demo()
        return svc.registry.get(repo_id)

    @router.get("/repos/{repo_id}/pulls")
    def pulls(repo_id: str, refresh: bool = False):
        return svc.pulls_for(repo_id, refresh)

    def all_pulls() -> tuple[list[dict], list[dict]]:
        items, errors = [], []
        for record in svc.registry.list():
            if record["status"] not in ("ready", "needs_clone"):
                continue
            try:
                items += [{**p, "repo_name": record["full_name"]} for p in svc.pulls_for(record["id"])]
            except (GitHubError, RepoError) as exc:
                errors.append({"repo_id": record["id"], "repo_name": record["full_name"], "message": str(exc)})
        return items, errors

    @router.get("/pulls")
    def pulls_everywhere():
        items, errors = all_pulls()
        return {"pulls": items, "errors": errors}

    @router.get("/overview")
    def overview():
        items, errors = all_pulls()
        runs = svc.runs.list_runs(limit=500)
        decided = {}
        for run in runs:  # newest first: the first completed run per PR is its current decision
            if run["status"] == "completed":
                decided.setdefault((run["repo_id"], str(run["pr"].get("ref"))), run["decision"])
        week = time.time() - 7 * 86400
        return {
            "repositories": len(svc.registry.list()), "open_pulls": len(items),
            "awaiting_analysis": sum(1 for p in items if p["latest_run"] is None or p["stale"]),
            "running": sum(r["status"] == "running" for r in runs), "queued": sum(r["status"] == "queued" for r in runs),
            "decisions": {d: sum(1 for v in decided.values() if v == d) for d in ("PASS", "CANARY", "BLOCK")},
            "completed_7d": sum(1 for r in runs if r["status"] == "completed" and (r["ended_at"] or 0) > week),
            "recent_runs": runs[:8], "errors": errors,
        }

    @router.get("/activity")
    def activity(limit: int = 30):
        return svc.runs.activity(min(limit, 100))

    @router.get("/repos/{repo_id}/pulls/{ref}")
    def pull(repo_id: str, ref: str):
        repo_record = svc.registry.get(repo_id)
        if repo_record["kind"] == "github":
            if not ref.isdigit():
                raise RepoError("Pull request number must be an integer.", 422, "invalid_pr")
            data = svc.github.pull(repo_record["owner"], repo_record["name"], int(ref))
            data["ci"] = svc.github.checks(repo_record["owner"], repo_record["name"], data["head_sha"])
        else:
            data = next((p for p in svc.registry.pulls(repo_id) if p["ref"] == ref), None)
            if data is None:
                raise RepoError("Unknown branch.", 404, "not_found")
            data["ci"] = None
        data["runs"] = [m for m in svc.runs.list_runs(repo_id=repo_id) if str(m["pr"].get("ref")) == ref]
        data["repo"] = {k: repo_record.get(k) for k in ("id", "kind", "full_name", "url", "default_branch", "trusted", "status")}
        return data

    # --- analyses ---------------------------------------------------------------------------
    @router.post("/analyses", status_code=202)
    def start(body: AnalyzeRequest):
        if body.repo_id != "unierp-demo" and not body.ref.isdigit():
            raise RepoError("Pull request number must be an integer.", 422, "invalid_pr")
        return svc.runs.submit(body.repo_id, body.ref, llm=body.llm, publish=body.publish)

    @router.get("/analyses")
    def analyses(repo_id: str | None = None, status: str | None = None, limit: int = 100):
        return svc.runs.list_runs(repo_id=repo_id, status=status, limit=min(limit, 500))

    @router.get("/analyses/compare")
    def compare(a: str, b: str):
        pair = []
        for run_id in (a, b):
            meta, report = svc.runs.get_meta(run_id), svc.runs.get_report(run_id)
            if meta is None or report is None:
                raise RepoError(f"Run {run_id} has no completed report to compare.", 404, "not_found")
            pair.append((meta, report))
        return compare_runs(pair[0][0], pair[0][1], pair[1][0], pair[1][1])

    @router.get("/analyses/{run_id}")
    def analysis(run_id: str):
        meta = svc.runs.get_meta(run_id)
        if meta is None:
            raise RepoError("Unknown run.", 404, "not_found")
        return {"meta": meta, "report": svc.runs.get_report(run_id) if meta["status"] == "completed" else None}

    @router.get("/analyses/{run_id}/events")
    def events(run_id: str, after: int = 0):
        log = svc.runs.get_log(run_id)
        if log is None:
            raise RepoError("Unknown run.", 404, "not_found")
        return {"events": log.since(max(after, 0)), "closed": log.closed, "status": (svc.runs.get_meta(run_id) or {}).get("status")}

    @router.get("/analyses/{run_id}/stream")
    def stream(run_id: str, request: Request, after: int = 0):
        log = svc.runs.get_log(run_id)
        if log is None:
            raise RepoError("Unknown run.", 404, "not_found")
        header = request.headers.get("last-event-id", "")
        seq = max(after, int(header) if header.isdigit() else 0)

        def generate():
            nonlocal seq
            yield "retry: 2000\n\n"
            while True:
                batch = log.wait(seq, timeout=15)
                for event in batch:
                    seq = event["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(event, default=str)}\n\n"
                if not batch:
                    if log.closed:
                        yield "event: end\ndata: {}\n\n"
                        return
                    yield ": keep-alive\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/analyses/{run_id}/publish")
    def publish_info(run_id: str):
        meta = svc.runs.get_meta(run_id)
        pr_head = None
        number = ((meta or {}).get("pr") or {}).get("number")
        if meta and number and meta.get("repo_id") and svc.registry.get(meta["repo_id"]).get("kind") == "github":
            repo = svc.registry.get(meta["repo_id"])
            try:
                pr_head = svc.github.pull(repo["owner"], repo["name"], int(number))["head_sha"]
            except (GitHubError, KeyError):
                pr_head = None  # staleness is advisory; publishing is judged on the run's own commit
        return svc.runs.publish_info(run_id, pr_head)

    @router.post("/analyses/{run_id}/publish")
    def publish(run_id: str, body: PublishRequest):
        return svc.runs.publish_existing(run_id, force=body.force, targets=body.targets)

    @router.post("/analyses/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        return svc.runs.cancel(run_id)

    # --- deployments (staging canary) ---------------------------------------------------------------
    @router.get("/deployments/targets")
    def deployment_targets():
        from sentinel.verify import sandbox as sbx

        docker = sbx.docker_status()
        return [{"name": t.name, "kind": t.kind, "environment": t.environment, "capabilities": capabilities(t),
                 "ready": docker["available"] if t.kind == "docker" else True,
                 "note": None if t.kind != "docker" or docker["available"] else docker["detail"]} for t in svc.deployments.targets.values()]

    @router.get("/deployments")
    def deployments():
        return svc.deployments.list()

    @router.post("/deployments", status_code=202)
    def deploy(body: DeployRequest):
        return svc.deployments.create(target=body.target, stable_ref=body.stable_ref, candidate_ref=body.candidate_ref, step_seconds=body.step_seconds,
                                      slo={"max_error_rate": body.max_error_rate, "p95_ratio": body.p95_ratio}, test_fault=body.test_fault, confirm=body.confirm, run_id=body.run_id)

    @router.get("/deployments/{dep_id}")
    def deployment(dep_id: str):
        return {"state": svc.deployments.get(dep_id), "audit": svc.deployments.audit(dep_id)}

    @router.post("/deployments/{dep_id}/{action}", status_code=202)
    def deployment_control(dep_id: str, action: str):
        return svc.deployments.control(dep_id, action)

    @router.get("/github/check")
    def github_check(repo: str | None = None):
        """Is the server's GitHub token valid, and what can it reach? Never returns the token."""
        return svc.github.access(repo)

    @router.get("/github/app")
    def github_app(repo: str | None = None):
        """The GitHub App used for Check Runs: configured? installed on this repository? allowed Checks: write? No key or token is returned."""
        return svc.app_auth.describe(repo)

    # --- language models --------------------------------------------------------------------------
    @router.get("/llm/status")
    def llm_status():
        from sentinel.llm.health import status as llm

        return llm(svc.cfg)

    @router.post("/llm/check")
    def llm_check(body: LlmCheck):
        from sentinel.llm.health import check_provider

        return check_provider(body.provider, svc.cfg, probe=body.probe)

    # --- model ----------------------------------------------------------------------------------
    @router.get("/model")
    def model():
        from sentinel.risk.card import model_card

        return model_card(svc.cfg)

    return router


def error_handlers(app) -> None:
    @app.exception_handler(RepoError)
    async def repo_error(_request, exc: RepoError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(DeployError)
    async def deploy_error(_request, exc: DeployError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(GitHubError)
    async def github_error(_request, exc: GitHubError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code)

