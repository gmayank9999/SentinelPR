"""Publish results to GitHub: a Check Run (the merge gate) and a single, updated PR comment."""

from __future__ import annotations

import logging

import httpx

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

    def upsert_comment(self, pr_number: int, body: str, marker: str) -> int:
        """Create the SentinelPR comment, or edit it in place on later pushes."""
        page = 1
        while True:
            response = self.client.get(f"/repos/{self.repository}/issues/{pr_number}/comments", params={"per_page": 100, "page": page})
            response.raise_for_status()
            comments = response.json()
            for comment in comments:
                if marker in (comment.get("body") or ""):
                    edit = self.client.patch(f"/repos/{self.repository}/issues/comments/{comment['id']}", json={"body": body})
                    edit.raise_for_status()
                    return comment["id"]
            if len(comments) < 100:
                break
            page += 1
        created = self.client.post(f"/repos/{self.repository}/issues/{pr_number}/comments", json={"body": body})
        created.raise_for_status()
        return created.json()["id"]

    def check_run(self, head_sha: str, name: str, report: dict, prefix: str = "") -> int:
        output = check_run_output(report, prefix)
        annotations = output.pop("annotations")
        payload = {
            "name": name, "head_sha": head_sha, "status": "completed",
            "conclusion": CONCLUSION[report["decision"]],
            "output": {**output, "annotations": annotations[:50]},
        }
        response = self.client.post(f"/repos/{self.repository}/check-runs", json=payload)
        response.raise_for_status()
        return response.json()["id"]


def publish(report: dict, cfg, repository: str, token: str, *, head_sha: str) -> dict:
    publisher = GitHubPublisher(repository, token)
    marker = cfg.get("publish.comment_marker", "<!-- sentinelpr:report -->")
    result: dict = {}
    number = report["pr"].get("number")
    try:
        result["check_run"] = publisher.check_run(head_sha, cfg.get("publish.check_name", "SentinelPR Gate"), report, cfg.project_prefix)
    except httpx.HTTPStatusError as exc:
        # Fork PRs get a read-only token; the job's own status still carries the decision.
        log.warning("could not create check run: %s", exc.response.text[:300])
        result["check_run_error"] = exc.response.status_code
    if number:
        try:
            body = render_comment(report, marker, cfg.get("publish.dashboard_url"))
            result["comment"] = publisher.upsert_comment(number, body, marker)
        except httpx.HTTPStatusError as exc:
            log.warning("could not post PR comment: %s", exc.response.text[:300])
            result["comment_error"] = exc.response.status_code
    return result
