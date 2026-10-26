"""Publish results to GitHub: a Check Run (the merge gate) and a single, updated PR comment.

Each call is judged only by GitHub's answer: a Check Run or comment counts as created when GitHub returns a
2xx response that contains its id, and for any other outcome the status, GitHub's message and the permission it
asked for are kept (``check_run_detail`` / ``comment_detail``) so the caller can explain exactly what to change.
A failure of one call never prevents the other.
"""

from __future__ import annotations

import logging

import httpx

from sentinel.publish.diagnose import GitHubAPIError
from sentinel.publish.render import CONCLUSION, check_run_output, render_comment

log = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHubPublisher:
    def __init__(self, repository: str, token: str, client: httpx.Client | None = None):
        self.repository = repository
        self.client = client or httpx.Client(
            base_url=API, timeout=30,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One request; anything other than a 2xx answer becomes a GitHubAPIError carrying GitHub's own message."""
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubAPIError(0, f"{type(exc).__name__}") from exc
        if not 200 <= response.status_code < 300:
            raise GitHubAPIError.from_response(response)
        return response

    @staticmethod
    def _id(response: httpx.Response, what: str) -> int:
        try:
            value = response.json()["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubAPIError(response.status_code, f"GitHub answered successfully but returned no id for the {what}; it is not counted as created.") from exc
        if not isinstance(value, int):
            raise GitHubAPIError(response.status_code, f"GitHub returned an unexpected id for the {what}; it is not counted as created.")
        return value

    def upsert_comment(self, pr_number: int, body: str, marker: str) -> int:
        """Create the SentinelPR comment, or edit it in place on later pushes."""
        page = 1
        while True:
            comments = self._send("GET", f"/repos/{self.repository}/issues/{pr_number}/comments", params={"per_page": 100, "page": page}).json()
            for comment in comments:
                if marker in (comment.get("body") or ""):
                    return self._id(self._send("PATCH", f"/repos/{self.repository}/issues/comments/{comment['id']}", json={"body": body}), "comment")
            if len(comments) < 100:
                break
            page += 1
        return self._id(self._send("POST", f"/repos/{self.repository}/issues/{pr_number}/comments", json={"body": body}), "comment")

    def check_run_payload(self, head_sha: str, name: str, report: dict, prefix: str = "") -> dict:
        output = check_run_output(report, prefix)
        annotations = output.pop("annotations")
        return {"name": name, "head_sha": head_sha, "status": "completed", "conclusion": CONCLUSION[report["decision"]],
                "output": {**output, "annotations": annotations[:50]}}

    STATE = {"PASS": "success", "CANARY": "success", "BLOCK": "failure"}  # statuses have no "neutral": CANARY merges, and says so in its description

    def commit_status(self, head_sha: str, report: dict, context: str, target_url: str | None = None) -> int:
        """A commit status. A fine-grained token with Commit statuses: Read and write can create these, and a status context
        can be made a required check in branch protection (what a Check Run would otherwise be used for)."""
        reason = (report.get("decision_reason") or "").strip()
        description = f"{report['decision']}: risk {report.get('risk', 0):.2f}" + (f", {reason}" if reason else "")
        payload = {"state": self.STATE[report["decision"]], "context": context, "description": description[:140]}
        if target_url:
            payload["target_url"] = target_url
        return self._id(self._send("POST", f"/repos/{self.repository}/statuses/{head_sha}", json=payload), "commit status")

    def check_run(self, head_sha: str, name: str, report: dict, prefix: str = "") -> int:
        response = self._send("POST", f"/repos/{self.repository}/check-runs", json=self.check_run_payload(head_sha, name, report, prefix))
        return self._id(response, "Check Run")


_SAME = object()
TARGETS = ("comment", "status", "check_run")


def publish(report: dict, cfg, repository: str, token: str, *, head_sha: str, check_token=_SAME, targets: dict | None = None) -> dict:
    """Post the result. ``token`` posts the comment and commit status; ``check_token`` creates the Check Run.

    In GitHub Actions the workflow token can do both (the default). From the server, a personal token cannot create a Check Run, so
    ``check_token`` is a GitHub App installation token, or None, in which case the Check Run is *not attempted* and the result says so.
    """
    want = {t: (targets or {}).get(t, True) for t in TARGETS}
    result: dict = {}
    number = report["pr"].get("number")
    if want["check_run"]:
        use = token if check_token is _SAME else check_token
        if not use:
            result["check_run_skipped"] = "needs_github_app"
        else:
            try:
                result["check_run"] = GitHubPublisher(repository, use).check_run(head_sha, cfg.get("publish.check_name", "SentinelPR Gate"), report, cfg.project_prefix)
            except GitHubAPIError as exc:
                log.warning("could not create check run: %s", exc)
                result["check_run_error"] = exc.status
                result["check_run_detail"] = exc.detail()
    publisher = GitHubPublisher(repository, token)
    if want["status"]:
        try:
            result["status"] = publisher.commit_status(head_sha, report, cfg.get("publish.status_context", "SentinelPR / gate"), cfg.get("publish.dashboard_url"))
        except GitHubAPIError as exc:
            log.warning("could not create commit status: %s", exc)
            result["status_error"] = exc.status
            result["status_detail"] = exc.detail()
    if number and want["comment"]:
        marker = cfg.get("publish.comment_marker", "<!-- sentinelpr:report -->")
        try:
            body = render_comment(report, marker, cfg.get("publish.dashboard_url"))
            result["comment"] = publisher.upsert_comment(number, body, marker)
        except GitHubAPIError as exc:
            log.warning("could not post PR comment: %s", exc)
            result["comment_error"] = exc.status
            result["comment_detail"] = exc.detail()
    return result
