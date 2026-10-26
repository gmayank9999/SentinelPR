"""Authenticate as a GitHub App, the only way GitHub allows a Check Run to be created.

GitHub: "To create a check run, you must use a GitHub App. OAuth apps and authenticated users are not able to
create them" (docs.github.com/rest/checks/runs). Personal access tokens, fine-grained or classic, cannot, and have
no Checks permission to grant. The supported flow, implemented here exactly as GitHub documents it:

1. sign a JWT with the App's private key (RS256; ``iat`` 60 s in the past, ``exp`` under 10 minutes, ``iss`` = client or App id);
2. find the App's installation on the repository (``GET /repos/{owner}/{repo}/installation``);
3. exchange the JWT for an installation access token (``POST /app/installations/{id}/access_tokens``) limited to that one
   repository and to ``checks: write``. It lasts one hour and is cached in memory until five minutes before it expires.

Configuration comes from the server environment only: ``SENTINEL_GITHUB_APP_CLIENT_ID`` (or ``SENTINEL_GITHUB_APP_ID``),
``SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH`` (a path to the ``.pem`` file, never the key itself) and optionally
``SENTINEL_GITHUB_APP_INSTALLATION_ID``. Neither the key, the JWT nor an installation token is ever logged or returned.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

API = "https://api.github.com"
APPS_PAGE = "https://github.com/settings/apps"
REFRESH_BEFORE_S = 300


class AppAuthError(Exception):
    def __init__(self, code: str, message: str, fix: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix


@dataclass
class AppConfig:
    issuer: str | None            # the App's client id (preferred) or numeric App id
    key_path: Path | None
    installation_id: int | None = None

    @classmethod
    def from_env(cls) -> "AppConfig":
        issuer = os.getenv("SENTINEL_GITHUB_APP_CLIENT_ID") or os.getenv("SENTINEL_GITHUB_APP_ID") or None
        key = os.getenv("SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH")
        installation = os.getenv("SENTINEL_GITHUB_APP_INSTALLATION_ID", "")
        return cls(issuer, Path(key) if key else None, int(installation) if installation.isdigit() else None)

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.key_path)

    def problems(self) -> list[str]:
        out = []
        if not self.issuer:
            out.append("SENTINEL_GITHUB_APP_CLIENT_ID (or SENTINEL_GITHUB_APP_ID) is not set.")
        if not self.key_path:
            out.append("SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH is not set.")
        elif not self.key_path.is_file():
            out.append("SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH does not point to an existing file.")
        return out


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_private_key(path: Path):
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - exercised by a test that hides the module
        raise AppAuthError("crypto_missing", "The 'cryptography' package is not installed, so the App's JWT cannot be signed.", "Run: pip install cryptography") from exc
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except FileNotFoundError as exc:
        raise AppAuthError("key_missing", "The GitHub App private key file was not found.", "Check SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH.") from exc
    except (ValueError, TypeError) as exc:
        raise AppAuthError("key_invalid", "The file is not an unencrypted PEM RSA private key.",
                           "Use the .pem file GitHub gave you when you generated the App's private key (it must not be passphrase-protected).") from exc
    from cryptography.hazmat.primitives.asymmetric import rsa

    if not isinstance(key, rsa.RSAPrivateKey):
        raise AppAuthError("key_invalid", "The private key is not an RSA key; GitHub Apps sign with RS256.", "Generate the key from the App's settings page.")
    return key


def make_jwt(config: AppConfig, now: float | None = None) -> str:
    """RS256 JWT per GitHub's rules: iat backdated 60 s for clock drift, exp at most 10 minutes ahead."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(now if now is not None else time.time())
    signing = f"{_b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}, separators=(',', ':')).encode())}.{_b64(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': config.issuer}, separators=(',', ':')).encode())}"
    signature = load_private_key(config.key_path).sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64(signature)}"


def _fail(response: httpx.Response, step: str) -> AppAuthError:
    try:
        message = str(response.json().get("message", ""))[:240]
    except ValueError:
        message = ""
    code = response.status_code
    if code == 401:
        return AppAuthError("jwt_rejected", f"GitHub rejected the App's credentials ({step}, 401): {message}",
                            "Check the Client ID/App ID and that the private key belongs to this App. Also make sure this computer's clock is accurate to within a minute.")
    if code == 404 and step == "find installation":
        return AppAuthError("not_installed", "The GitHub App is not installed on this repository.",
                            "Open the App's settings → Install App → choose your account → select this repository (or All repositories) → Install.")
    if code == 422:
        return AppAuthError("permissions_not_granted", f"GitHub would not issue a Checks token ({step}, 422): {message}",
                            "Open the App's settings → Permissions & events → Repository permissions → Checks → Read and write → Save, then accept the new permission on the installation.")
    if code == 403:
        return AppAuthError("forbidden", f"GitHub refused ({step}, 403): {message}", "If this mentions rate limits, wait; otherwise review the App's permissions.")
    return AppAuthError("github_error", f"GitHub returned HTTP {code} ({step}): {message}", "Try again; if it persists, check https://www.githubstatus.com.")


class GitHubAppAuth:
    def __init__(self, config: AppConfig | None = None, http: httpx.Client | None = None, clock=time.time):
        self.config = config or AppConfig.from_env()
        self.http = http or httpx.Client(base_url=API, timeout=20, follow_redirects=False)
        self.clock = clock
        self._installations: dict[str, int] = {}
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _jwt_headers(self) -> dict:
        if not self.config.configured:
            raise AppAuthError("not_configured", "No GitHub App is configured.", "Set the App variables described in docs/github-app.md.")
        return {"Authorization": f"Bearer {make_jwt(self.config, self.clock())}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def _request(self, method: str, path: str, step: str, **kw) -> httpx.Response:
        try:
            response = self.http.request(method, path, headers=self._jwt_headers(), **kw)
        except httpx.HTTPError as exc:
            raise AppAuthError("unreachable", f"Could not reach GitHub ({step}): {type(exc).__name__}.", "Check your network connection.") from exc
        if response.status_code >= 300:
            raise _fail(response, step)
        return response

    def installation_id(self, repository: str) -> int:
        if self.config.installation_id:
            return self.config.installation_id
        with self._lock:
            if repository in self._installations:
                return self._installations[repository]
        value = self._request("GET", f"/repos/{repository}/installation", "find installation").json().get("id")
        if not isinstance(value, int):
            raise AppAuthError("github_error", "GitHub returned no installation id.", "")
        with self._lock:
            self._installations[repository] = value
        return value

    def token(self, repository: str, permissions: dict[str, str] | None = None) -> str:
        """A one-hour installation token limited to ``repository`` and ``permissions`` (default: checks write)."""
        permissions = permissions or {"checks": "write"}
        key = (repository, json.dumps(permissions, sort_keys=True))
        with self._lock:
            cached = self._tokens.get(key)
            if cached and cached[1] - self.clock() > REFRESH_BEFORE_S:
                return cached[0]
        name = repository.split("/", 1)[1]
        response = self._request("POST", f"/app/installations/{self.installation_id(repository)}/access_tokens", "create installation token",
                                 json={"repositories": [name], "permissions": permissions})
        data = response.json()
        token = data.get("token")
        if not token:
            raise AppAuthError("github_error", "GitHub returned no installation token.", "")
        try:
            expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            expires = self.clock() + 3600
        with self._lock:
            self._tokens[key] = (token, expires)
        return token

    def describe(self, repository: str | None = None) -> dict:
        """What the App is and can do, for the UI. Contains no key, JWT or token."""
        out: dict = {"configured": self.configured, "problems": self.config.problems(), "app": None, "installation": None, "checks_write": None}
        if not self.configured or out["problems"]:
            return out
        try:
            app = self._request("GET", "/app", "read the App").json()
            perms = app.get("permissions") or {}
            out["app"] = {"name": app.get("name"), "slug": app.get("slug"), "html_url": app.get("html_url"), "permissions": perms}
            out["checks_write"] = perms.get("checks") == "write"
            if not out["checks_write"]:
                out["problems"].append("The App does not have Checks: Read and write (App settings → Permissions & events → Repository permissions → Checks).")
            if repository:
                out["installation"] = {"id": self.installation_id(repository), "repository": repository}
        except AppAuthError as exc:
            out["problems"].append(f"{exc.message} {exc.fix}".strip())
        return out
