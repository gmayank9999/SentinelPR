"""Read-only GitHub client and safe repository acquisition.

The token (``GITHUB_TOKEN``) stays on the server: it is never returned by the API, never put
in a clone URL (so it cannot land in ``.git/config``) and is redacted from every event.
Everything user-supplied is validated before it reaches a URL, a path or a git argument.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

API = "https://api.github.com"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9_])?$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}$")
URL_RE = re.compile(r"^(?:https://github\.com/|github\.com/)?([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 502, code: str = "github_error"):
        super().__init__(message)
        self.status, self.code = status, code


def parse_repo_url(text: str) -> tuple[str, str]:
    """``https://github.com/o/r``, ``github.com/o/r`` or ``o/r`` -> (owner, name). Nothing else is accepted."""
    match = URL_RE.match((text or "").strip())
    if not match:
        raise GitHubError("Enter a GitHub repository as https://github.com/<owner>/<name> or <owner>/<name>.", 422, "invalid_repository")
    owner, name = match.groups()
    if not OWNER_RE.match(owner) or not NAME_RE.match(name) or name in {".", ".."} or ".." in name:
        raise GitHubError("That does not look like a valid GitHub owner/name.", 422, "invalid_repository")
    return owner, name


def valid_ref(ref: str) -> bool:
    return bool(REF_RE.match(ref or "")) and ".." not in ref and not ref.endswith(("/", ".lock"))


def token() -> str | None:
    return os.getenv("SENTINEL_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or None


class GitHubClient:
    def __init__(self, http: httpx.Client | None = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "SentinelPR"}
        if token():
            headers["Authorization"] = f"Bearer {token()}"
        self.http = http or httpx.Client(base_url=API, headers=headers, timeout=20)

    RETRIES = 2

    def _get(self, path: str, **params):
        last: Exception | None = None
        for attempt in range(self.RETRIES + 1):
            try:
                response = self.http.get(path, params=params or None)
            except httpx.HTTPError as exc:  # timeouts and connection errors are retried, then reported
                last = exc
                time.sleep(0.4 * (attempt + 1))
                continue
            if response.status_code in (502, 503, 504) and attempt < self.RETRIES:
                time.sleep(0.4 * (attempt + 1))
                continue
            return self._decode(response)
        raise GitHubError(f"Could not reach GitHub after {self.RETRIES + 1} attempts ({type(last).__name__}).", 503, "unreachable") from last

    @staticmethod
    def _decode(response: httpx.Response):
        code = response.status_code
        if 300 <= code < 400:  # never follow a redirect: a moved repository must be re-entered by its new URL
            raise GitHubError("GitHub says this repository has moved or been renamed; enter its current URL.", 409, "moved")
        if code == 404:
            hint = "" if token() else " If it is private, set GITHUB_TOKEN on the SentinelPR server."
            raise GitHubError(f"Repository or pull request not found, or not accessible with the configured credentials.{hint}", 404, "not_found")
        if code in (401, 403, 429):
            limited = code == 429 or response.headers.get("x-ratelimit-remaining") == "0"
            if limited:
                reset = response.headers.get("x-ratelimit-reset")
                when = f" It resets at {time.strftime('%H:%M:%S', time.localtime(int(reset)))}." if reset and reset.isdigit() else ""
                raise GitHubError("GitHub API rate limit reached." + when + ("" if token() else " Set GITHUB_TOKEN to raise the limit from 60 to 5,000 requests per hour."), 429, "rate_limited")
            raise GitHubError("GitHub rejected the credentials, or the token lacks access to this repository.", 403 if code == 403 else 401, "forbidden")
        if code >= 400:
            raise GitHubError(f"GitHub returned HTTP {code}.", 502)
        return response.json()

    @property
    def authenticated(self) -> bool:
        return token() is not None

    def access(self, repo: str | None = None) -> dict:
        """What the configured token can do, without ever returning it. The token's kind is read from its prefix only."""
        value = token() or ""
        kind = "classic personal access token" if value.startswith("ghp_") else "fine-grained personal access token" if value.startswith("github_pat_")             else "GitHub App / other token" if value else None
        out: dict = {"configured": bool(value), "token_kind": kind, "valid": None, "login": None, "rate_limit": None, "scopes": None, "repository": None, "problems": []}
        if not value:
            out["problems"].append("No GITHUB_TOKEN is configured on the server.")
            return out
        try:
            response = self.http.get("/rate_limit")
        except httpx.HTTPError as exc:
            out["problems"].append(f"Could not reach GitHub ({type(exc).__name__}).")
            return out
        if response.status_code == 401:
            out.update(valid=False)
            out["problems"].append("GitHub rejected the token (401): it is wrong, revoked or expired.")
            return out
        out["valid"] = response.status_code == 200
        core = (response.json().get("resources", {}).get("core", {}) if response.status_code == 200 else {})
        out["rate_limit"] = {"limit": core.get("limit"), "remaining": core.get("remaining")}
        scopes = response.headers.get("x-oauth-scopes")
        out["scopes"] = [x.strip() for x in scopes.split(",") if x.strip()] if scopes is not None else None
        try:
            user = self.http.get("/user")
            if user.status_code == 200:
                out["login"] = user.json().get("login")
        except httpx.HTTPError:
            pass
        if repo:
            owner, name = parse_repo_url(repo)
            r = self.http.get(f"/repos/{owner}/{name}")
            if r.status_code == 200:
                data = r.json()
                out["repository"] = {"full_name": data["full_name"], "private": data["private"], "permissions": data.get("permissions")}
                if not (data.get("permissions") or {}).get("pull"):
                    out["problems"].append("The token cannot read this repository.")
            elif r.status_code in (403, 404):
                out["repository"] = {"full_name": f"{owner}/{name}", "accessible": False}
                out["problems"].append("The token cannot see this repository (404/403): grant it access to the repository, or the repository does not exist.")
        if core.get("limit") == 60:
            out["problems"].append("GitHub treats requests as unauthenticated (limit 60/hour): the token is not being used.")
        return out

    def repo(self, owner: str, name: str) -> dict:
        data = self._get(f"/repos/{owner}/{name}")
        return {"owner": data["owner"]["login"], "name": data["name"], "full_name": data["full_name"], "private": data["private"],
                "default_branch": data["default_branch"], "description": data.get("description") or "", "language": data.get("language"),
                "html_url": data["html_url"], "clone_url": data["clone_url"], "size_kb": data.get("size", 0),
                "stars": data.get("stargazers_count", 0), "pushed_at": data.get("pushed_at")}

    def languages(self, owner: str, name: str) -> dict[str, int]:
        return self._get(f"/repos/{owner}/{name}/languages")

    def pulls(self, owner: str, name: str, state: str = "open", limit: int = 30) -> list[dict]:
        return [self._pull(p) for p in self._get(f"/repos/{owner}/{name}/pulls", state=state, per_page=min(limit, 100), sort="updated", direction="desc")]

    def pull(self, owner: str, name: str, number: int) -> dict:
        return self._pull(self._get(f"/repos/{owner}/{name}/pulls/{int(number)}"))

    def checks(self, owner: str, name: str, sha: str) -> dict | None:
        """CI state of a commit, when GitHub reports one."""
        try:
            data = self._get(f"/repos/{owner}/{name}/commits/{sha}/status")
        except GitHubError:
            return None
        return {"state": data.get("state"), "total": data.get("total_count", 0)}

    @staticmethod
    def _pull(p: dict) -> dict:
        head_repo = (p["head"].get("repo") or {}).get("full_name")
        base_repo = (p["base"].get("repo") or {}).get("full_name")
        return {"number": p["number"], "title": p["title"], "body": p.get("body") or "", "author": (p.get("user") or {}).get("login", ""),
                "state": p["state"], "draft": p.get("draft", False), "html_url": p["html_url"],
                "base_ref": p["base"]["ref"], "base_sha": p["base"]["sha"], "head_ref": p["head"]["ref"], "head_sha": p["head"]["sha"],
                "is_fork": bool(head_repo and base_repo and head_repo != base_repo), "labels": [l["name"] for l in p.get("labels", [])],
                "changed_files": p.get("changed_files"), "additions": p.get("additions"), "deletions": p.get("deletions"),
                "commits": p.get("commits"), "created_at": p.get("created_at"), "updated_at": p.get("updated_at")}


# --- safe git acquisition --------------------------------------------------------


def _git_env() -> dict:
    """Environment for git. The token travels here (GIT_CONFIG_*), not on the command line where any local
    user could read it from the process list."""
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "SENTINEL_GITHUB_TOKEN", "GROQ_API_KEY", "GEMINI_API_KEY", "SENTINEL_API_TOKEN")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ASKPASS": "echo", "GIT_LFS_SKIP_SMUDGE": "1"})
    if token():
        basic = base64.b64encode(f"x-access-token:{token()}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"})
    return env


def _git_prefix() -> list[str]:
    """Hardening for every git call that talks to the network or checks out untrusted content."""
    prefix = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.symlinks=false",
              "-c", "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null", "-c", "submodule.recurse=false"]
    return prefix


def run_git(args: list[str], cwd: Path | None = None, timeout: float = 900) -> str:
    cmd = _git_prefix() + args
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, env=_git_env())
    except subprocess.TimeoutExpired as exc:
        raise GitHubError(f"git {args[0]} timed out after {int(timeout)}s", 504, "timeout") from exc
    if result.returncode != 0:
        from sentinel.service.events import redact

        raise GitHubError(f"git {args[0]} failed: {redact(result.stderr.strip())[:400]}", 502, "git_failed")
    return result.stdout


@dataclass
class Checkout:
    path: Path
    head_sha: str
    base_sha: str


def clone_or_update(owner: str, name: str, destination: Path, timeout: float = 900) -> Path:
    """Clone ``https://github.com/owner/name`` into ``destination`` (validated by the caller to lie inside the service dir)."""
    url = f"https://github.com/{owner}/{name}.git"
    if (destination / ".git").exists():
        run_git(["fetch", "--quiet", "--no-tags", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*"], destination, timeout)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--quiet", "--no-tags", "--no-recurse-submodules", "--", url, str(destination)], None, timeout)
    return destination


def fetch_pull(repo_path: Path, number: int, base_ref: str, timeout: float = 600) -> None:
    """Fetch the PR head (works for forks too) and the base branch so both SHAs exist locally."""
    if not valid_ref(base_ref):
        raise GitHubError("Unsafe base branch name.", 422, "invalid_ref")
    run_git(["fetch", "--quiet", "--no-tags", "origin", f"+refs/pull/{int(number)}/head:refs/sentinel/pr/{int(number)}",
             f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}"], repo_path, timeout)
