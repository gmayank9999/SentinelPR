"""Deployment targets: where a baseline and a candidate version run, and how traffic is split between them.

A target provisions the two versions and exposes a *router* that can shift a weight to the candidate.
Everything the rollout engine concludes is measured from the applications themselves (their own request
counters and the version header on every response), never from a target's claim that it did something.

    local     two uvicorn processes on this machine behind the built-in weighted proxy
    docker    two containers (built from the two commits) behind the built-in weighted proxy
    external  a staging environment you operate: you deploy the candidate, SentinelPR drives and
              measures it through a router's admin API (PUT/GET /__canary/weight, /__canary/status)

Production is never a valid environment.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import uvicorn

from sentinel import gitutil
from sentinel.canary.proxy import create_proxy
from sentinel.canary.run import checkout, free_port, service, wait_healthy
from sentinel.verify import sandbox as sbx

log = logging.getLogger("sentinel.deploy")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
ENVIRONMENTS = {"local", "staging"}


class DeployError(Exception):
    def __init__(self, message: str, code: str = "deploy_error", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass
class Endpoints:
    stable_url: str       # the baseline, addressed directly (for its own metrics)
    candidate_url: str    # the candidate, addressed directly
    router_url: str       # the entry point users hit; splits traffic
    stable_version: str   # what the baseline's X-UniERP-Version header must say
    candidate_version: str
    notes: dict = field(default_factory=dict)


@dataclass
class TargetConfig:
    name: str
    kind: str                       # local | docker | external
    environment: str = "local"      # local | staging (production is refused)
    options: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.environment not in ENVIRONMENTS:
            raise DeployError(f"Target {self.name!r} has environment {self.environment!r}. Only {sorted(ENVIRONMENTS)} are supported: "
                              "SentinelPR does not deploy to production.", "production_refused", 403)
        if self.kind not in ("local", "docker", "external"):
            raise DeployError(f"Unknown target kind {self.kind!r}.", "invalid_target", 422)


def capabilities(config: TargetConfig) -> dict:
    """What a target can do, stated up front so the UI never promises more."""
    if config.kind == "external":
        has_router = bool(config.options.get("router_url"))
        return {"weighted_routing": has_router, "builds_candidate": False, "disposable": False,
                "note": None if has_router else "No router_url is configured, so this target cannot split traffic by weight; progressive rollout is unavailable."}
    return {"weighted_routing": True, "builds_candidate": True, "disposable": True, "note": None}


def resolve_ref(repo: Path, ref: str) -> str:
    if not (SHA_RE.match(ref) or re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}$", ref)) or ".." in ref:
        raise DeployError("Invalid ref.", "invalid_ref", 422)
    try:
        return gitutil.rev_parse(repo, ref)
    except gitutil.GitError as exc:
        raise DeployError(f"Cannot resolve {ref!r} in the application repository.", "unknown_ref", 404) from exc


# --- the router -----------------------------------------------------------------------------------------------


@contextmanager
def builtin_router(stable_url: str, candidate_url: str):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_proxy(stable_url, candidate_url), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    wait_healthy(url)
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class Router:
    """Talks to a router's admin API. Reading the weight back is what proves a change took effect."""

    def __init__(self, url: str, token: str | None = None):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def set_weight(self, weight: float) -> None:
        httpx.put(f"{self.url}/__canary/weight", json={"weight": weight}, headers=self.headers, timeout=10).raise_for_status()

    def get_weight(self) -> float:
        response = httpx.get(f"{self.url}/__canary/status", headers=self.headers, timeout=10)
        response.raise_for_status()
        return float(response.json()["weight"])


def probe(router_url: str, requests: int = 40) -> dict[str, int]:
    """Send real requests through the router and count which *application version* answered (from its own header)."""
    seen: dict[str, int] = {}
    with httpx.Client(base_url=router_url, timeout=10) as client:
        for _ in range(requests):
            try:
                response = client.get("/courses")
                version = response.headers.get("X-UniERP-Version", f"no-version-header({response.status_code})")
            except httpx.HTTPError:
                version = "unreachable"
            seen[version] = seen.get(version, 0) + 1
    return seen


# --- targets ----------------------------------------------------------------------------------------------------------


class Target:
    def __init__(self, config: TargetConfig, app_repo: Path, app_dir: str = "."):
        config.validate()
        self.config, self.app_repo, self.app_dir = config, Path(app_repo), app_dir

    def provision(self, stable_sha: str, candidate_sha: str, candidate_env: dict, emit):
        raise NotImplementedError


class LocalTarget(Target):
    @contextmanager
    def provision(self, stable_sha, candidate_sha, candidate_env, emit):
        scratch = Path(tempfile.mkdtemp(prefix="deploy-"))
        try:
            with ExitStack() as stack:
                stable_dir = stack.enter_context(checkout(self.app_repo, stable_sha, self.app_dir, scratch, "stable"))
                cand_dir = stack.enter_context(checkout(self.app_repo, candidate_sha, self.app_dir, scratch, "candidate"))
                sv, cv = f"stable-{stable_sha[:7]}", f"candidate-{candidate_sha[:7]}"
                emit("provision", "starting the baseline and the candidate as local processes")
                stable_url = stack.enter_context(service(stable_dir, sv, {}))
                cand_url = stack.enter_context(service(cand_dir, cv, candidate_env))
                router = stack.enter_context(builtin_router(stable_url, cand_url))
                yield Endpoints(stable_url, cand_url, router, sv, cv, {"kind": "local processes"})
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class DockerTarget(Target):
    """Builds an image per commit and runs each as a locked-down container; the router stays on the host."""

    def _run(self, args: list[str], timeout: float = 600) -> str:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode != 0:
            raise DeployError(f"docker {args[0]} failed: {proc.stderr.strip()[-300:]}", "docker_failed", 502)
        return proc.stdout.strip()

    @contextmanager
    def provision(self, stable_sha, candidate_sha, candidate_env, emit):
        if not sbx.docker_status()["available"]:
            raise DeployError("The Docker target needs a running Docker engine: " + sbx.docker_status()["detail"], "docker_unavailable", 409)
        deploy_id = uuid.uuid4().hex[:8]
        scratch = Path(tempfile.mkdtemp(prefix="deploy-"))
        images, containers = [], []
        try:
            with ExitStack() as stack:
                built = {}
                for role, sha in (("stable", stable_sha), ("candidate", candidate_sha)):
                    tree = stack.enter_context(checkout(self.app_repo, sha, self.app_dir, scratch, role))
                    image = f"sentinelpr-canary-{deploy_id}-{role}"
                    emit("provision", f"building the {role} image from commit {sha[:10]}")
                    self._run(["build", "-q", "-t", image, str(tree)], timeout=900)
                    images.append(image)
                    built[role] = image
                urls, versions = {}, {"stable": f"stable-{stable_sha[:7]}", "candidate": f"candidate-{candidate_sha[:7]}"}
                for role in ("stable", "candidate"):
                    name = f"sentinelpr-canary-{deploy_id}-{role}"
                    env = {"UNIERP_VERSION": versions[role], **(candidate_env if role == "candidate" else {})}
                    flags = ["run", "-d", "--name", name, "--label", f"sentinelpr.deploy={deploy_id}", "-p", "127.0.0.1::8000", "--read-only", "--tmpfs", "/tmp",
                             "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "512m", "--cpus", "1", "--pids-limit", "200"]
                    for key, value in env.items():
                        flags += ["-e", f"{key}={value}"]
                    self._run([*flags, built[role]])
                    containers.append(name)
                    port = self._run(["port", name, "8000/tcp"]).splitlines()[0].rsplit(":", 1)[1]
                    urls[role] = f"http://127.0.0.1:{port}"
                    emit("provision", f"{role} container {name} is listening on {urls[role]}")
                for url in urls.values():
                    wait_healthy(url, timeout_s=60)
                router = stack.enter_context(builtin_router(urls["stable"], urls["candidate"]))
                yield Endpoints(urls["stable"], urls["candidate"], router, versions["stable"], versions["candidate"], {"kind": "docker containers", "containers": containers})
        finally:
            for name in containers:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
            for image in images:
                subprocess.run(["docker", "rmi", "-f", image], capture_output=True, timeout=60)
            shutil.rmtree(scratch, ignore_errors=True)


class ExternalTarget(Target):
    """A staging environment the operator runs. Nothing is built or started here; SentinelPR only routes and measures."""

    @contextmanager
    def provision(self, stable_sha, candidate_sha, candidate_env, emit):
        o = self.config.options
        if not capabilities(self.config)["weighted_routing"]:
            raise DeployError(capabilities(self.config)["note"], "no_weighted_routing", 409)
        for key in ("stable_url", "candidate_url", "router_url"):
            if not o.get(key):
                raise DeployError(f"External target {self.config.name!r} needs {key}.", "invalid_target", 422)
        emit("provision", "using the operator's staging environment; the candidate must already be deployed there")
        for url in (o["stable_url"], o["candidate_url"]):
            wait_healthy(url, timeout_s=30)
        yield Endpoints(o["stable_url"], o["candidate_url"], o["router_url"], o.get("stable_version", ""), o.get("candidate_version", ""), {"kind": "external staging"})


def build_target(config: TargetConfig, app_repo: Path, app_dir: str = ".") -> Target:
    return {"local": LocalTarget, "docker": DockerTarget, "external": ExternalTarget}[config.kind](config, app_repo, app_dir)


def sweep_deploy_containers() -> int:
    if not sbx.docker_status()["available"]:
        return 0
    ids = subprocess.run(["docker", "ps", "-aq", "--filter", "label=sentinelpr.deploy"], capture_output=True, text=True, timeout=30).stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=60)
    return len(ids)
