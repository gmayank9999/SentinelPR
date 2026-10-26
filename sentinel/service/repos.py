"""Connected repositories: the registry, compatibility checks and preparing a PR for analysis.

Two kinds of repository exist:

* ``demo``    the bundled UniERP project (a seeded git repo; its "pull requests" are branches).
* ``github``  any repository on github.com the server can read. It is cloned under the service
              directory and analysed from there.

Analysing a repository executes its test suite (and mutants of its code) on this machine, so a
GitHub repository must be explicitly marked trusted first. The demo repository is ours.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sentinel import gitutil
from sentinel.agents.base import PullRequest
from sentinel.config import PACKAGE_DIR, Config
from sentinel.orchestrator.observe import Emit, no_emit
from sentinel.service import github
from sentinel.service.github import GitHubClient, GitHubError
from sentinel.verify import sandbox as sbx

DEMO_ID = "unierp-demo"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")
IGNORED_PACKAGES = {"tests", "test", "docs", "doc", "examples", "example", "scripts", "benchmarks", "tools", "build", "dist", "venv", ".venv"}


class RepoError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "repo_error"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class AnalysisSpec:
    cfg: Config
    base: str
    head: str
    pr: PullRequest
    info: dict  # PR facts recorded with the run (refs, urls, size)


def slug(owner: str, name: str) -> str:
    return f"{owner}--{name}".lower()


# --- project detection ----------------------------------------------------------


def detect_project(path: Path, name: str, python_share: float | None) -> dict:
    """What the verifier needs to know about a repository, plus an honest compatibility report."""
    checks: list[dict] = []

    def check(cid: str, ok: bool | None, label: str, detail: str = "") -> bool | None:
        checks.append({"id": cid, "ok": ok, "label": label, "detail": detail})
        return ok

    if python_share is not None:
        check("language", python_share >= 0.5, "Python is the main language",
              f"{python_share:.0%} of the code is Python" + ("" if python_share >= 0.5 else "; the verifier supports Python with pytest only"))
    manifests = [m for m in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt") if (path / m).exists()]
    check("manifest", bool(manifests), "Python project metadata", ", ".join(manifests) or "no pyproject.toml / setup.py / requirements.txt found")
    tests = next((d for d in ("tests", "test") if (path / d).is_dir()), None)
    check("tests", tests is not None, "pytest test directory", f"{tests}/" if tests else "no tests/ or test/ directory")
    package = None
    if path.exists():
        src = path / "src"
        roots = [src] if src.is_dir() else [path]
        candidates = [d.name for r in roots for d in sorted(r.iterdir()) if d.is_dir() and (d / "__init__.py").exists() and d.name not in IGNORED_PACKAGES]
        normal = re.sub(r"[-.]", "_", name.lower())
        package = next((c for c in candidates if c.lower() == normal), candidates[0] if candidates else None)
    check("package", package is not None, "importable package", package or "no top-level package with __init__.py found")
    check("dependencies", None, "Dependencies installed",
          "SentinelPR does not install a repository's dependencies; its tests run in SentinelPR's own Python environment, so a repository whose "
          "tests need other packages will report a collection error.")
    hard = [c for c in checks if c["ok"] is False]
    level = "unsupported" if any(c["id"] in ("language", "tests") and c["ok"] is False for c in checks) else ("limited" if hard else "full")
    return {"project": {"root": ".", "package": package, "tests": tests or "tests"}, "compat": {"level": level, "checks": checks}}


# --- registry ------------------------------------------------------------------


class RepoRegistry:
    def __init__(self, cfg: Config, service_dir: Path):
        self.cfg = cfg
        self.dir = service_dir
        self.file = service_dir / "repos.json"
        self._lock = threading.RLock()
        self._seeding = False
        service_dir.mkdir(parents=True, exist_ok=True)

    # persistence
    def _load(self) -> dict[str, dict]:
        if not self.file.exists():
            return {}
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self, repos: dict[str, dict]) -> None:
        self.file.write_text(json.dumps(repos, indent=1), encoding="utf-8")

    # the demo repository -----------------------------------------------------------
    @property
    def demo_path(self) -> Path:
        return self.cfg.repo_root / ".sentinel" / "uni-erp-repo"

    def demo_record(self) -> dict:
        ready = (self.demo_path / ".git").exists()
        status = "seeding" if self._seeding else "ready" if ready else "needs_seed"
        return {"id": DEMO_ID, "kind": "demo", "owner": "sentinelpr", "name": "UniERP (bundled demo)", "full_name": "sentinelpr/unierp-demo",
                "url": None, "default_branch": "main", "description": "University ERP service with a scripted 63-commit history, seeded bugs and issues.",
                "private": False, "language": "Python", "python_share": 1.0, "trusted": True, "status": status,
                "project": {"root": ".", "package": "unierp", "tests": "tests"},
                "compat": {"level": "full", "checks": [{"id": "bundled", "ok": True, "label": "Bundled and trusted", "detail": "Part of this repository; its history is rebuilt deterministically."}]},
                "added_at": None, "last_synced_at": None}

    def seed_demo(self) -> None:
        if self._seeding or (self.demo_path / ".git").exists():
            return
        self._seeding = True

        def work() -> None:
            try:
                script = self.cfg.repo_root / "demo" / "uni-erp" / "history" / "seed_history.py"
                subprocess.run([sys.executable, str(script), "--out", str(self.demo_path)], check=True, capture_output=True, timeout=900)
            finally:
                self._seeding = False

        threading.Thread(target=work, daemon=True).start()

    # queries ---------------------------------------------------------------------------
    def list(self) -> list[dict]:
        with self._lock:
            return [self.demo_record()] + sorted(self._load().values(), key=lambda r: r["added_at"] or 0, reverse=True)

    def get(self, repo_id: str) -> dict:
        if repo_id == DEMO_ID:
            return self.demo_record()
        if not SLUG_RE.match(repo_id or ""):
            raise RepoError("Unknown repository.", 404, "not_found")
        with self._lock:
            record = self._load().get(repo_id)
        if record is None:
            raise RepoError("Unknown repository.", 404, "not_found")
        return record

    def checkout_path(self, repo_id: str) -> Path:
        path = (self.dir / "repos" / repo_id / "src").resolve()
        if (self.dir / "repos").resolve() not in path.parents:
            raise RepoError("Invalid repository path.", 400, "invalid_path")
        return path

    # connecting -----------------------------------------------------------------------
    def validate(self, text: str, client: GitHubClient | None = None) -> dict:
        """Check a repository is reachable and describe it, without cloning anything."""
        owner, name = github.parse_repo_url(text)
        client = client or GitHubClient()
        info = client.repo(owner, name)
        languages = client.languages(owner, name)
        total = sum(languages.values()) or 1
        share = languages.get("Python", 0) / total
        # package and tests are only known after cloning, so the compatibility report is partial until then
        compat = {"level": "unsupported" if share < 0.5 else "pending",
                  "checks": [{"id": "language", "ok": share >= 0.5, "label": "Python is the main language",
                              "detail": f"{share:.0%} Python" + ("" if share >= 0.5 else "; the verifier supports Python with pytest only")}]}
        return {**info, "python_share": round(share, 4), "languages": languages, "compat": compat,
                "authenticated": client.authenticated, "already_connected": slug(info["owner"], info["name"]) in self._load()}

    def connect(self, text: str, client: GitHubClient | None = None) -> dict:
        info = self.validate(text, client)
        repo_id = slug(info["owner"], info["name"])
        if not SLUG_RE.match(repo_id):
            raise RepoError("Repository name cannot be used as an identifier.", 422, "invalid_repository")
        record = {"id": repo_id, "kind": "github", "owner": info["owner"], "name": info["name"], "full_name": info["full_name"],
                  "url": info["html_url"], "default_branch": info["default_branch"], "description": info["description"], "private": info["private"],
                  "language": info["language"], "python_share": info["python_share"], "trusted": False,
                  "status": "unsupported" if info["compat"]["level"] == "unsupported" else "needs_clone",
                  "project": {"root": ".", "package": None, "tests": "tests"}, "compat": info["compat"], "added_at": time.time(), "last_synced_at": None}
        with self._lock:
            repos = self._load()
            if repo_id in repos:
                return repos[repo_id]
            repos[repo_id] = record
            self._save(repos)
        return record

    def update(self, repo_id: str, **fields) -> dict:
        with self._lock:
            repos = self._load()
            if repo_id not in repos:
                raise RepoError("Unknown repository.", 404, "not_found")
            repos[repo_id].update(fields)
            self._save(repos)
            return repos[repo_id]

    def remove(self, repo_id: str) -> None:
        with self._lock:
            repos = self._load()
            if repo_id not in repos:
                raise RepoError("Unknown repository.", 404, "not_found")
            del repos[repo_id]
            self._save(repos)

    def set_trusted(self, repo_id: str, trusted: bool) -> dict:
        if repo_id == DEMO_ID:
            raise RepoError("The bundled demo repository is always trusted.", 400, "not_applicable")
        return self.update(repo_id, trusted=bool(trusted))

    # pull requests ------------------------------------------------------------------------
    def pulls(self, repo_id: str, client: GitHubClient | None = None) -> list[dict]:
        repo = self.get(repo_id)
        if repo["kind"] == "demo":
            return self._demo_branches(repo)
        return (client or GitHubClient()).pulls(repo["owner"], repo["name"])

    def _demo_branches(self, repo: dict) -> list[dict]:
        if repo["status"] != "ready":
            return []
        path = self.demo_path
        base = repo["default_branch"]
        rows = gitutil.git(path, "for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(authorname)\t%(committerdate:iso-strict)\t%(subject)", "refs/heads").splitlines()
        base_sha = gitutil.rev_parse(path, base)
        out = []
        for row in rows:
            ref, sha, author, date, subject = (row.split("\t") + [""] * 5)[:5]
            if ref == base:
                continue
            ahead = int(gitutil.git(path, "rev-list", "--count", f"{base}..{ref}", check=False).strip() or 0)
            if ahead == 0:
                continue
            stat = gitutil.git(path, "diff", "--shortstat", f"{base}...{ref}", check=False).strip()
            out.append({"number": None, "ref": ref, "title": subject, "body": "", "author": author, "state": "open", "draft": False, "html_url": None,
                        "base_ref": base, "base_sha": base_sha, "head_ref": ref, "head_sha": sha, "is_fork": False, "labels": [],
                        "commits": ahead, "summary": stat, "updated_at": date})
        return sorted(out, key=lambda p: p["updated_at"], reverse=True)

    # preparing an analysis --------------------------------------------------------------------
    def execution_mode(self, repo: dict) -> str:
        """Where this repository's code may run: a Docker sandbox whenever one is available, otherwise only on the
        host and only if the operator explicitly trusted the repository. Never a silent fallback."""
        if repo["kind"] == "demo":
            return "host"
        if sbx.docker_status()["available"]:
            return "docker"
        if repo.get("trusted"):
            return "host"
        raise RepoError("Docker is not available, so this repository cannot be sandboxed: " + sbx.docker_status()["detail"]
                        + " You can start Docker, or explicitly trust the repository to run its tests unsandboxed on this machine.", 403, "not_trusted")

    def prepare(self, repo_id: str, ref: str, emit: Emit = no_emit, client: GitHubClient | None = None, run_id: str | None = None) -> AnalysisSpec:
        repo = self.get(repo_id)
        if repo["kind"] == "demo":
            return self._prepare_demo(repo, ref)
        mode = self.execution_mode(repo)
        if repo["status"] == "unsupported":
            raise RepoError("This repository is not supported by the verifier (Python with pytest only).", 422, "unsupported")
        try:
            number = int(ref)
        except ValueError as exc:
            raise RepoError("Pull request number must be an integer.", 422, "invalid_pr") from exc
        client = client or GitHubClient()
        emit("intake", "log", "info", f"fetching pull request #{number} from {repo['full_name']}")
        pr = client.pull(repo["owner"], repo["name"], number)
        path = self.checkout_path(repo_id)
        started = time.time()
        emit("intake", "log", "info", "cloning the repository" if not (path / ".git").exists() else "updating the local clone")
        github.clone_or_update(repo["owner"], repo["name"], path)
        github.fetch_pull(path, number, pr["base_ref"])
        emit("intake", "log", "info", f"clone ready in {time.time() - started:.1f}s")
        detected = detect_project(path, repo["name"], repo.get("python_share"))
        self.update(repo_id, project=detected["project"], compat=detected["compat"], status="ready" if detected["compat"]["level"] != "unsupported" else "unsupported",
                    last_synced_at=time.time())
        if detected["compat"]["level"] == "unsupported":
            raise RepoError("This repository is not supported by the verifier: " + "; ".join(c["detail"] for c in detected["compat"]["checks"] if c["ok"] is False), 422, "unsupported")
        head_sha = gitutil.rev_parse(path, f"refs/sentinel/pr/{number}")
        if head_sha != pr["head_sha"]:
            emit("intake", "log", "warn", f"the pull request moved while it was being fetched; analysing the fetched head {head_sha[:10]}")
        base_sha = self._ensure_commit(path, pr["base_sha"], pr["base_ref"], emit)
        deps_dir = None
        if mode == "docker":
            emit("intake", "log", "info", f"execution: Docker sandbox ({sbx.docker_status()['detail']})", {"execution": "docker"})
            deps_dir = self._prepare_deps(path, head_sha, repo_id, emit)
        else:
            emit("intake", "log", "warn", "execution: ON THE HOST, unsandboxed (Docker is unavailable and the repository was explicitly trusted)", {"execution": "host"})
        cfg = self._project_cfg(path, repo_id, detected["project"], mode, deps_dir, run_id)
        return AnalysisSpec(
            cfg, base_sha, head_sha,
            PullRequest(number=number, title=pr["title"], body=pr["body"], author=pr["author"], base_sha=pr["base_sha"], head_sha=head_sha,
                        labels=pr["labels"], is_fork=pr["is_fork"]),
            {**{k: pr.get(k) for k in ("number", "title", "author", "html_url", "base_ref", "head_ref", "changed_files", "additions", "deletions", "commits", "is_fork", "draft")},
             "execution": mode},
        )

    @staticmethod
    def _ensure_commit(path: Path, sha: str, base_ref: str, emit: Emit) -> str:
        """The base SHA GitHub reports, or (if the base branch was rewritten since) the merge base with its tip."""
        try:
            return gitutil.rev_parse(path, sha)
        except gitutil.GitError:
            pass
        try:
            github.run_git(["fetch", "--quiet", "--no-tags", "origin", sha], path, 300)
            return gitutil.rev_parse(path, sha)
        except (GitHubError, gitutil.GitError):
            fallback = gitutil.merge_base(path, f"origin/{base_ref}", "HEAD")
            emit("intake", "log", "warn", f"base commit {sha[:10]} is no longer reachable; using the merge base {fallback[:10]}")
            return fallback

    def _prepare_deps(self, path: Path, head_sha: str, repo_id: str, emit: Emit) -> Path | None:
        """Install the PR head's declared test dependencies inside a sandbox (network allowed only for this step)."""
        names = [n for n in gitutil.git(path, "ls-tree", "-r", "--name-only", head_sha).splitlines()
                 if n == "pyproject.toml" or (n.startswith("requirements") and n.endswith(".txt") and "/" not in n) or n == "tests/requirements.txt"]
        manifests = {}
        for name in names:
            text = gitutil.show_file(path, head_sha, name)
            if text is not None and len(text) < 200_000:
                manifests[name] = text.encode("utf-8")
        emit("intake", "log", "info", f"installing declared dependencies in a sandbox ({', '.join(sorted(manifests)) or 'no manifests found'}); "
                                       "this container has network access, sees only the manifests, and holds no credentials")
        spec = sbx.SandboxSpec(mode="docker")
        try:
            return sbx.prepare_dependencies(spec, manifests, self.dir / "repos" / repo_id / "deps")
        except sbx.SandboxError as exc:
            raise RepoError(str(exc), 422, exc.code) from exc

    def _prepare_demo(self, repo: dict, ref: str) -> AnalysisSpec:
        if repo["status"] != "ready":
            raise RepoError("The demo repository has not been seeded yet.", 409, "needs_seed")
        if not github.valid_ref(ref):
            raise RepoError("Invalid branch name.", 422, "invalid_ref")
        path = self.demo_path
        try:
            head_sha = gitutil.rev_parse(path, f"refs/heads/{ref}")
        except gitutil.GitError as exc:
            raise RepoError(f"Unknown branch {ref!r}.", 404, "not_found") from exc
        base = repo["default_branch"]
        cfg = Config.load(path, path=self.dir / "none.yaml", environ={}, overrides={
            "project": {"root": ".", "package": "unierp", "tests": "tests", "tracker_dir": str(self.cfg.repo_root / ".sentinel" / "uni-erp-repo-meta")},
            "workdir": str(self.cfg.repo_root / ".sentinel"), "risk": {"model_path": str(PACKAGE_DIR / "risk" / "artifacts" / "model.json")},
            "sandbox": {"mode": "host"},  # the bundled demo is part of this project
        })
        subject = gitutil.git(path, "log", "-1", "--format=%s", head_sha).strip()
        author = gitutil.git(path, "log", "-1", "--format=%an", head_sha).strip()
        return AnalysisSpec(cfg, gitutil.rev_parse(path, base), head_sha, PullRequest(number=None, title=subject, body="", author=author, head_sha=head_sha),
                            {"number": None, "ref": ref, "title": subject, "author": author, "base_ref": base, "head_ref": ref})

    def _project_cfg(self, checkout: Path, repo_id: str, project: dict, mode: str = "host", deps_dir: Path | None = None, run_id: str | None = None) -> Config:
        # The analysed repository's own sentinel.yaml is never read, and the risk model always
        # comes from SentinelPR: a pull request must not be able to change how it is scored.
        return Config.load(checkout, path=self.dir / "none.yaml", environ={}, overrides={
            "project": {**project, "history_repo": None, "tracker_dir": None},
            "workdir": str(self.dir / "repos" / repo_id / "work"),
            "risk": {"model_path": str(PACKAGE_DIR / "risk" / "artifacts" / "model.json")},
            "sandbox": {"mode": mode, "deps_dir": str(deps_dir) if deps_dir else None, "run_id": run_id},
        })
