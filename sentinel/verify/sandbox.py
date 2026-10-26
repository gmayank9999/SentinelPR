"""Run a repository's tests in a Docker container instead of on the host.

Untrusted code (a pull request's tests, its mutants, its dependencies) never runs in the
SentinelPR process. Every test run is a short-lived container with:

* no network (``--network none``), no capabilities, no new privileges, a read-only root filesystem
* CPU, memory, process-count and file-size limits, and a wall-clock timeout that kills the container
* only three things mounted: a disposable copy of the project (read-write), a scratch directory for
  reports (read-write) and the dependency directory (read-only). SentinelPR's pytest plugin is staged
  as a separate read-only mount. No host environment variable, credential, SSH key, Git config or
  Docker socket reaches it.

Dependencies are installed by a *separate* container that has network access but only ever sees the
project's dependency manifests, never its source or any secret.

There is deliberately no fallback: when Docker is unavailable a sandboxed run fails with
``SandboxError`` instead of silently executing on the host.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.config import PACKAGE_DIR

log = logging.getLogger("sentinel.sandbox")

LABEL = "sentinelpr.sandbox=1"
BASE_IMAGE = "python:3.11-slim"
IMAGE_TAG = "sentinelpr-sandbox:1"
DOCKERFILE = f"""FROM {BASE_IMAGE}
RUN pip install --no-cache-dir pytest pytest-cov coverage && useradd -u 65534 -M -s /usr/sbin/nologin sandbox 2>/dev/null || true
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /work
"""

# What the dependency installer reads from a project: nothing else is mounted into that container.
MANIFESTS = ("requirements.txt", "requirements-dev.txt", "requirements-test.txt", "requirements_test.txt", "dev-requirements.txt", "test-requirements.txt",
             "tests/requirements.txt", "pyproject.toml")
TEST_EXTRAS = {"test", "tests", "testing", "dev", "develop"}
INSTALL_SCRIPT = r"""
import pathlib, subprocess, sys, tomllib
root = pathlib.Path("/manifest")
args = []
for name in sorted(p.relative_to(root).as_posix() for p in root.rglob("requirements*.txt")) + ["tests/requirements.txt"]:
    if (root / name).is_file() and ["-r", str(root / name)] not in args:
        args.append(["-r", str(root / name)])
pp = root / "pyproject.toml"
specs = []
if pp.is_file():
    data = tomllib.loads(pp.read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs += project.get("dependencies", [])
    for extra, deps in project.get("optional-dependencies", {}).items():
        if extra.lower() in %(extras)r:
            specs += deps
    for group, deps in data.get("dependency-groups", {}).items():
        if group.lower() in %(extras)r:
            specs += [d for d in deps if isinstance(d, str)]
cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--target", "/deps", "--disable-pip-version-check"]
for a in args:
    cmd += a
cmd += specs
if not args and not specs:
    print("no dependency manifests found; nothing to install")
    sys.exit(0)
sys.exit(subprocess.call(cmd))
""" % {"extras": TEST_EXTRAS}


class SandboxError(RuntimeError):
    def __init__(self, message: str, code: str = "sandbox_error"):
        super().__init__(message)
        self.code = code


@dataclass
class SandboxSpec:
    """How to run untrusted tests. ``mode`` is "docker" or "host" (an explicit, operator-granted exception)."""

    mode: str = "host"
    image: str = IMAGE_TAG
    memory: str = "1g"
    cpus: str = "1.5"
    pids: int = 256
    tmpfs_mb: int = 256
    fsize_mb: int = 256          # largest single file a process may write
    deps_dir: Path | None = None  # read-only dependency directory (see ``prepare_dependencies``)
    network: str = "none"
    stage_dir: Path | None = None  # where the plugin mount is staged
    extra: dict = field(default_factory=dict)

    @property
    def docker(self) -> bool:
        return self.mode == "docker"

    @classmethod
    def from_config(cls, cfg, **overrides) -> "SandboxSpec":
        get = lambda key, default: cfg.get(f"sandbox.{key}", default)  # noqa: E731
        spec = cls(mode=str(get("mode", "host")), image=str(get("image", IMAGE_TAG)), memory=str(get("memory", "1g")), cpus=str(get("cpus", "1.5")),
                   pids=int(get("pids", 256)), tmpfs_mb=int(get("tmpfs_mb", 256)), fsize_mb=int(get("fsize_mb", 256)))
        if get("run_id", None):
            spec.extra["run_id"] = str(get("run_id", None))
        if get("deps_dir", None):
            spec.deps_dir = Path(str(get("deps_dir", None)))
        for key, value in overrides.items():
            setattr(spec, key, value)
        return spec


# --- availability --------------------------------------------------------------------------------

_status: tuple[float, dict] | None = None


def docker_status(max_age: float = 10.0) -> dict:
    """{"available": bool, "version": str|None, "detail": str}. Cached briefly; never raises."""
    global _status
    if _status and time.time() - _status[0] < max_age:
        return _status[1]
    if shutil.which("docker") is None:
        result = {"available": False, "version": None, "detail": "The docker command was not found on PATH."}
    else:
        try:
            out = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                result = {"available": True, "version": out.stdout.strip(), "detail": f"Docker engine {out.stdout.strip()}"}
            else:
                result = {"available": False, "version": None, "detail": "Docker is installed but its engine is not running (start Docker Desktop or the docker service)."}
        except (subprocess.TimeoutExpired, OSError):
            result = {"available": False, "version": None, "detail": "Docker did not respond within 10 seconds."}
    _status = (time.time(), result)
    return result


def require_docker() -> None:
    status = docker_status()
    if not status["available"]:
        raise SandboxError(f"Sandboxed execution needs Docker, which is unavailable: {status['detail']} "
                           "SentinelPR will not fall back to running the repository on the host.", "sandbox_unavailable")


_image_lock = threading.Lock()


def ensure_image(spec: SandboxSpec) -> None:
    require_docker()
    with _image_lock:
        if subprocess.run(["docker", "image", "inspect", spec.image], capture_output=True).returncode == 0:
            return
        log.info("building sandbox image %s", spec.image)
        build = subprocess.run(["docker", "build", "-t", spec.image, "-"], input=DOCKERFILE, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=900)
        if build.returncode != 0:
            raise SandboxError(f"Could not build the sandbox image: {build.stderr.strip()[-400:]}", "sandbox_image")


def _plugin_dir(stage: Path) -> Path:
    """The pytest plugin and nothing else of SentinelPR, staged as its own mount."""
    target = stage / "plugin"
    package = target / "sentinel"
    (package / "verify").mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "verify" / "__init__.py").write_text("", encoding="utf-8")
    source = PACKAGE_DIR / "verify" / "pytest_plugin.py"
    dest = package / "verify" / "pytest_plugin.py"
    if not dest.exists() or dest.read_bytes() != source.read_bytes():
        shutil.copyfile(source, dest)
    return target


def _uid() -> str:
    """The container runs as an unprivileged user: the host user on Linux (so bind mounts are writable), else 'nobody'."""
    return f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") and os.getuid() != 0 else "65534:65534"


def _open_to_sandbox_user(path: Path) -> None:
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass


def _mount(source: Path, target: str, readonly: bool = False) -> list[str]:
    spec = f"type=bind,source={Path(source).resolve()},target={target}" + (",readonly" if readonly else "")
    return ["--mount", spec]


def hardening_flags(spec: SandboxSpec, name: str, network: str | None = None) -> list[str]:
    run_label = ["--label", f"sentinelpr.run={spec.extra['run_id']}"] if spec.extra.get("run_id") else []
    return [
        *run_label, "--rm", "--name", name, "--label", LABEL, "--network", network or spec.network, "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,size={spec.tmpfs_mb}m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", str(spec.pids), "--memory", spec.memory, "--memory-swap", spec.memory, "--cpus", spec.cpus,
        "--ulimit", f"fsize={spec.fsize_mb * 1024 * 1024}", "--ulimit", "nofile=2048", "--user", _uid(), "--workdir", "/work",
        "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
    ]


# --- running jobs ----------------------------------------------------------------------------------


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    container: str = ""


_running: dict[str, str] = {}  # job id -> container name, for cancellation
_running_lock = threading.Lock()


def kill_container(name: str) -> None:
    subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def cancel_run(run_id: str) -> int:
    """Kill every sandbox container that belongs to an analysis run (called when the user cancels it)."""
    if not docker_status()["available"]:
        return 0
    ids = subprocess.run(["docker", "ps", "-q", "--filter", f"label=sentinelpr.run={run_id}"], capture_output=True, text=True, timeout=30).stdout.split()
    for cid in ids:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)
    return len(ids)


def cancel(job: str) -> bool:
    with _running_lock:
        name = _running.get(job)
    if name:
        kill_container(name)
    return bool(name)


def _run(args: list[str], name: str, timeout_s: float, job: str | None = None) -> Result:
    with _running_lock:
        if job:
            _running[job] = name
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8", errors="replace")
        return Result(proc.returncode, proc.stdout, proc.stderr, False, name)
    except subprocess.TimeoutExpired:
        kill_container(name)  # killing the docker CLI alone would leave the container running
        return Result(-1, "", "timed out", True, name)
    finally:
        with _running_lock:
            _running.pop(job or "", None)


def sweep_orphans() -> int:
    """Remove sandbox containers left behind by a crashed run."""
    if not docker_status()["available"]:
        return 0
    ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=60)
    return len(ids)


def run_pytest(spec: SandboxSpec, *, project_dir: Path, scratch: Path, pytest_args: list[str], package: str | None, coverage: bool,
               timeout_s: float, stop_first: bool = False, disposable: bool = False, job: str | None = None, test_ids: list[str] | None = None) -> Result:
    """Run pytest inside a container against a disposable copy of ``project_dir``.

    ``scratch`` receives ``report.json`` and ``coverage.dat``. ``disposable=True`` means the caller
    already made ``project_dir`` a throwaway copy (mutation workspaces) and it is mounted as is.
    """
    ensure_image(spec)
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    _open_to_sandbox_user(scratch)
    stage = Path(spec.stage_dir or scratch.parent / "sandbox-stage").resolve()
    stage.mkdir(parents=True, exist_ok=True)
    copy: Path | None = None
    workdir = Path(project_dir).resolve()
    if not disposable:
        from sentinel.verify.runner import IGNORED

        copy = Path(tempfile.mkdtemp(prefix="sbx-", dir=stage))
        workdir = copy / "work"
        shutil.copytree(project_dir, workdir, ignore=IGNORED, symlinks=True)
    name = f"sentinelpr-{uuid.uuid4().hex[:12]}"
    try:
        src = workdir / "src"
        pythonpath = ["/plugin"] + (["/work/src"] if src.is_dir() and not (src / "__init__.py").exists() else []) + (["/deps"] if spec.deps_dir else []) + ["/work"]
        has_config = any((workdir / n).exists() for n in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"))
        if not has_config:
            (scratch / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        if test_ids is not None:
            (scratch / "tests.args").write_text("\n".join(test_ids), encoding="utf-8")
        cmd = ["python", "-m", "pytest", *pytest_args, "-p", "sentinel.verify.pytest_plugin", "-p", "no:cacheprovider", "-q", "-o", "addopts=", "--no-header", "-rN",
               "--rootdir=/work"]
        if not has_config:
            cmd += ["-c", "/scratch/pytest.ini"]
        if coverage and package:
            cmd += [f"--cov={package}", "--cov-context=test", "--cov-report="]
        if stop_first:
            cmd.append("-x")
        args = ["docker", "run", *hardening_flags(spec, name), *_mount(workdir, "/work"), *_mount(scratch, "/scratch"), *_mount(_plugin_dir(stage), "/plugin", True)]
        if spec.deps_dir:
            args += _mount(spec.deps_dir, "/deps", True)
        args += ["-e", f"PYTHONPATH={':'.join(pythonpath)}", "-e", "COVERAGE_FILE=/scratch/coverage.dat", "-e", "SENTINEL_TEST_REPORT=/scratch/report.json",
                 spec.image, *cmd]
        return _run(args, name, timeout_s, job)
    finally:
        if copy is not None:
            shutil.rmtree(copy, ignore_errors=True)


def project_digest(manifest_files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(manifest_files):
        digest.update(name.encode() + b"\0" + manifest_files[name] + b"\0")
    return digest.hexdigest()[:16]


def prepare_dependencies(spec: SandboxSpec, manifests: dict[str, bytes], cache_dir: Path, timeout_s: float = 900) -> Path:
    """Install a project's declared test dependencies into ``cache_dir/<digest>`` using a networked container
    that sees only the manifests. Returns the directory (cached by manifest content)."""
    ensure_image(spec)
    target = Path(cache_dir) / project_digest(manifests)
    if (target / ".ok").exists():
        return target
    shutil.rmtree(target, ignore_errors=True)
    (target / "deps").mkdir(parents=True)
    _open_to_sandbox_user(target / "deps")
    with tempfile.TemporaryDirectory(prefix="manifest-", dir=target) as tmp:
        for rel, content in manifests.items():
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        name = f"sentinelpr-deps-{uuid.uuid4().hex[:10]}"
        # pip writes only to /tmp (tmpfs) and the mounted /deps; the root filesystem stays read-only
        args = ["docker", "run", *hardening_flags(spec, name, network="bridge"), *_mount(Path(tmp), "/manifest", True), *_mount(target / "deps", "/deps"), spec.image, "python", "-c", INSTALL_SCRIPT]
        result = _run(args, name, timeout_s)
    if result.timed_out:
        raise SandboxError("Installing the repository's dependencies timed out.", "deps_timeout")
    if result.returncode != 0:
        raise SandboxError(f"Installing the repository's dependencies failed: {(result.stderr or result.stdout).strip()[-500:]}", "deps_failed")
    (target / ".ok").write_text(json.dumps({"digest": target.name, "at": time.time()}), encoding="utf-8")
    return target / "deps"
