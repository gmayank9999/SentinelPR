"""Issue and pull request data, from the GitHub API or from exported JSON files."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
API = "https://api.github.com"


def load_json_tracker(directory: Path) -> tuple[list[dict], list[dict]]:
    issues_file, pulls_file = directory / "issues.json", directory / "pulls.json"
    issues = json.loads(issues_file.read_text(encoding="utf-8")) if issues_file.exists() else []
    pulls = json.loads(pulls_file.read_text(encoding="utf-8")) if pulls_file.exists() else []
    return issues, pulls


class GitHubTracker:
    """Fetch issues, pull requests and their discussions for ``owner/repo``."""

    def __init__(self, repository: str, token: str | None = None, client: httpx.Client | None = None):
        self.repository = repository
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.Client(base_url=API, headers=headers, timeout=30)

    def _paginate(self, path: str, params: dict | None = None, limit: int = 1000) -> list[dict]:
        items: list[dict] = []
        url: str | None = path
        params = {"per_page": 100, **(params or {})}
        while url and len(items) < limit:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            items.extend(response.json())
            url = response.links.get("next", {}).get("url")
            params = None  # the "next" link already carries them
        return items[:limit]

    def fetch(self, since: str | None = None, limit: int = 500) -> tuple[list[dict], list[dict]]:
        params = {"state": "all", "sort": "updated", "direction": "desc"}
        if since:
            params["since"] = since
        raw = self._paginate(f"/repos/{self.repository}/issues", params, limit)
        issues, pulls = [], []
        for item in raw:
            comments = []
            if item.get("comments"):
                comments = [
                    {"author": c["user"]["login"], "body": c.get("body") or ""}
                    for c in self._paginate(item["comments_url"].replace(API, ""), limit=50)
                ]
            if "pull_request" in item:
                pulls.append(self._pull(item, comments))
            else:
                issues.append(
                    {
                        "number": item["number"],
                        "title": item["title"],
                        "body": item.get("body") or "",
                        "labels": [label["name"] for label in item.get("labels", [])],
                        "author": item["user"]["login"],
                        "state": item["state"],
                        "comments": comments,
                        "linked_prs": [],
                        "created_at": item.get("created_at"),
                        "closed_at": item.get("closed_at"),
                    }
                )
        return issues, pulls

    def _pull(self, item: dict, comments: list[dict]) -> dict:
        detail = self.client.get(f"/repos/{self.repository}/pulls/{item['number']}")
        merge_sha = merged_at = None
        if detail.status_code == 200:
            data = detail.json()
            merge_sha = data.get("merge_commit_sha") if data.get("merged_at") else None
            merged_at = data.get("merged_at")
        from sentinel.data.history import closing_issues

        return {
            "number": item["number"],
            "title": item["title"],
            "body": item.get("body") or "",
            "author": item["user"]["login"],
            "reviews": comments,
            "linked_issues": closing_issues(item.get("body") or ""),
            "merge_commit_sha": merge_sha,
            "merged_at": merged_at,
        }


def load_tracker(tracker_dir: Path | None, repository: str | None = None) -> tuple[list[dict], list[dict]]:
    """Prefer exported JSON; otherwise query GitHub when a repository and token are available."""
    if tracker_dir and tracker_dir.exists():
        return load_json_tracker(tracker_dir)
    repository = repository or os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if repository and token:
        try:
            return GitHubTracker(repository, token).fetch()
        except httpx.HTTPError as exc:
            log.warning("could not fetch tracker data from GitHub: %s", exc)
    return [], []


def link_issues_to_prs(issues: list[dict], pulls: list[dict]) -> None:
    """Fill ``issue.linked_prs`` from the pull requests that reference each issue."""
    by_number = {i["number"]: i for i in issues}
    for pr in pulls:
        for number in pr.get("linked_issues", []):
            issue = by_number.get(number)
            if issue is not None and pr["number"] not in issue.setdefault("linked_prs", []):
                issue["linked_prs"].append(pr["number"])
