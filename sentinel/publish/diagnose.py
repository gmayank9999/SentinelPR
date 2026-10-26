"""Understand why GitHub refused a publish call, and check Check-Run permission without creating anything.

GitHub's response is the source of truth. Its 403 for a missing permission reads "Resource not accessible by
personal access token" (or "...by integration" for a GitHub App) and the ``X-Accepted-GitHub-Permissions``
header names the permission the endpoint needs (``checks=write`` for Create a check run). Nothing here guesses
beyond that: when the cause cannot be determined the message says so and quotes GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

API = "https://api.github.com"
TOKENS_PAGE = "GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens"


class GitHubAPIError(Exception):
    """A non-2xx answer from GitHub (status 0 means the request never got one)."""

    def __init__(self, status: int, message: str, accepted: str | None = None, documentation_url: str | None = None, request_id: str | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.message, self.accepted, self.documentation_url, self.request_id = status, message, accepted, documentation_url, request_id

    def detail(self) -> dict:
        return {"status": self.status, "message": self.message, "accepted_permissions": self.accepted, "documentation_url": self.documentation_url}

    @classmethod
    def from_response(cls, response: httpx.Response) -> "GitHubAPIError":
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = body.get("message") if isinstance(body, dict) else None
        return cls(response.status_code, str(message or response.reason_phrase or "no message")[:300], response.headers.get("x-accepted-github-permissions"),
                   body.get("documentation_url") if isinstance(body, dict) else None, response.headers.get("x-github-request-id"))


def token_kind(token: str | None) -> str:
    if not token:
        return "none"
    return "classic" if token.startswith("ghp_") else "fine-grained" if token.startswith("github_pat_") else "other"


APP_DOC = "docs/github-app.md"


def explain(what: str, error: dict, kind: str = "fine-grained") -> dict:
    """{"explanation", "fix"} for a failed ``what`` ("check_run", "status" or "comment"), from GitHub's own detail."""
    status, message = error.get("status"), (error.get("message") or "").lower()
    label = {"check_run": "Check Run", "status": "commit status", "comment": "pull request comment"}[what]
    perm = {"check_run": "a GitHub App with Checks: Read and write", "status": "Commit statuses → Read and write",
            "comment": "Pull requests → Read and write (and Issues → Read and write)"}[what]
    token_fix = f"Open {TOKENS_PAGE} → your token → Repository permissions → {perm} → Update. Repository access and the other permissions are kept; the token string does not change, so .env needs no edit."
    if status == 0:
        return {"explanation": f"Could not reach GitHub to create the {label}: {error.get('message')}.", "fix": "Check your network connection and try again."}
    if status == 401:
        return {"explanation": f"GitHub rejected the credentials (401) while creating the {label}: they are invalid, expired or revoked.",
                "fix": "For a personal token, create a new one and put it in .env as GITHUB_TOKEN. For a GitHub App, check the Client ID and private key. Then restart the server."}
    if status == 403 and "rate limit" in message:
        return {"explanation": f"GitHub is rate limiting these credentials (403): {error.get('message')}.", "fix": "Wait a few minutes and try again."}
    if what == "check_run" and status == 403 and ("personal access token" in message or kind in ("classic", "fine-grained")):
        return {"explanation": "GitHub refused (403): only a GitHub App can create Check Runs. A personal access token, fine-grained or classic, cannot, and fine-grained tokens have no Checks permission to grant.",
                "fix": f"Set up a GitHub App ({APP_DOC}), or rely on the commit status, which a personal token can create."}
    if status == 403 and "personal access token" in message:
        return {"explanation": f"GitHub refused the {label} (403): this token does not have the permission it requires.", "fix": token_fix}
    if status == 403 and "integration" in message:
        return {"explanation": f"GitHub refused the {label} (403): the GitHub App installation lacks the permission.",
                "fix": "Open the App's settings → Permissions & events → set the permission to Read and write, then accept the updated permissions on the installation."}
    if status == 403 and not message:
        return {"explanation": f"GitHub refused the {label} (403) and did not say why. The usual cause is missing permission.", "fix": token_fix}
    if status == 403:
        return {"explanation": f"GitHub refused the {label} (403): {error.get('message')}", "fix": "See the message above; if it mentions SAML SSO, authorise the token for the organisation."}
    if status == 404:
        return {"explanation": f"GitHub could not find what the {label} targets (404): the repository, or the commit.",
                "fix": "Check that the credentials have access to this repository and that the commit was pushed to it."}
    if status == 422:
        return {"explanation": f"GitHub rejected the content of the {label} (422): {error.get('message')}", "fix": "The commit may not exist on GitHub, or the data was invalid; re-run the analysis."}
    return {"explanation": f"GitHub returned HTTP {status} for the {label}: {error.get('message')}", "fix": "Try again; if it persists, check the server log."}
