"""Slash commands in PR and issue comments.

    /sentinel ask <question>   answer from the repository, with citations
    /sentinel explain          why the latest decision was made (evidence and trace)
    /sentinel rerun            analyse the PR again and republish
    /sentinel fix              propose a fix as a draft PR (issues, or PRs with failures)

Only honoured for repository owners, members and collaborators.

    python -m sentinel.commands --event "$GITHUB_EVENT_PATH"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx

from sentinel import gitutil
from sentinel.agents.base import PullRequest
from sentinel.config import Config

log = logging.getLogger("sentinel.commands")
ALLOWED = {"OWNER", "MEMBER", "COLLABORATOR"}
COMMAND = re.compile(r"^/sentinel\s+(ask|explain|rerun|fix)\b\s*(.*)$", re.IGNORECASE | re.MULTILINE | re.DOTALL)
BOT = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")


def parse(body: str) -> tuple[str, str] | None:
    match = COMMAND.search(body or "")
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip().strip('"').strip()


class GitHub:
    def __init__(self, repository: str, token: str):
        self.repo = repository
        self.client = httpx.Client(base_url="https://api.github.com", timeout=30,
                                   headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def comment(self, number: int, body: str) -> None:
        self.client.post(f"/repos/{self.repo}/issues/{number}/comments", json={"body": body}).raise_for_status()

    def react(self, comment_id: int, content: str = "eyes") -> None:
        self.client.post(f"/repos/{self.repo}/issues/comments/{comment_id}/reactions", json={"content": content})

    def pull(self, number: int) -> dict:
        response = self.client.get(f"/repos/{self.repo}/pulls/{number}")
        response.raise_for_status()
        return response.json()

    def open_draft_pr(self, head: str, base: str, title: str, body: str) -> dict:
        response = self.client.post(f"/repos/{self.repo}/pulls", json={"head": head, "base": base, "title": title, "body": body, "draft": True})
        response.raise_for_status()
        return response.json()


def answer_question(cfg: Config, question: str) -> str:
    from sentinel.agents.qa import QABot
    from sentinel.data.index import open_store
    from sentinel.llm.client import LLMClient
    from sentinel.retrieval.hybrid import HybridRetriever, RetrievalIndex

    bot = QABot(HybridRetriever(RetrievalIndex.load(cfg, open_store(cfg))), LLMClient(cfg))
    answer = bot.answer(question)
    lines = [f"> {question}", "", answer.answer]
    if answer.citations:
        lines += ["", "<sub>Sources: " + ", ".join(f"`{c}`" for c in answer.citations) + "</sub>"]
    return "\n".join(lines)


def latest_run(cfg: Config, pr_number: int) -> dict | None:
    from sentinel.data.index import open_store

    for run in open_store(cfg).runs(limit=200):
        if run.get("pr", {}).get("number") == pr_number:
            return run
    return None


def analyse_pr(cfg: Config, pr_data: dict) -> dict:
    from sentinel.run import analyse

    pr = PullRequest(number=pr_data["number"], title=pr_data["title"], body=pr_data.get("body") or "",
                     author=pr_data["user"]["login"], base_sha=pr_data["base"]["sha"], head_sha=pr_data["head"]["sha"])
    return analyse(cfg, base=pr.base_sha, head=None, pr=pr)


def explain(report: dict) -> str:
    from sentinel.publish.render import details, headline, summary_lines

    return "\n".join([headline(report), "", *summary_lines(report), "", report.get("explanation", ""), "", details(report)])


def fix(cfg: Config, gh: GitHub, number: int, problem: str, seeds: list[str], report: dict | None, base_branch: str) -> str:
    from sentinel.agents.patch_advisor import PatchAdvisor, remediation_plan
    from sentinel.data.index import open_store
    from sentinel.llm.client import LLMClient
    from sentinel.retrieval.hybrid import HybridRetriever, RetrievalIndex

    llm = LLMClient(cfg)
    if not llm.available:
        return remediation_plan(report or {})
    index = RetrievalIndex.load(cfg, open_store(cfg))
    advisor = PatchAdvisor(cfg, HybridRetriever(index), llm, index.sources)
    proposal = advisor.propose(problem, seeds)
    if proposal is None:
        return "Patch Advisor could not produce a usable patch for this problem."
    tests = list((report or {}).get("selection", {}).get("tests", []))
    advisor.validate(proposal, tests, cfg.workdir / "patch")
    if not proposal.valid:
        reasons = proposal.problems + [e.message for e in (proposal.review.events if proposal.review else []) if e.severity == "block"]
        if proposal.tests and proposal.tests.failed:
            reasons.append(f"{len(proposal.tests.failed)} test(s) fail with the patch")
        return "Patch Advisor's proposal was rejected before opening a PR:\n" + "\n".join(f"- {r}" for r in reasons)

    branch = f"sentinel/fix-{number}"
    repo = cfg.repo_root
    gitutil.git(repo, "checkout", "-B", branch)
    for path, content in proposal.files.items():
        target = cfg.project_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    gitutil.git(repo, "add", "-A", cfg.get("project.root", "."))
    gitutil.git(repo, "-c", f"user.name={BOT[0]}", "-c", f"user.email={BOT[1]}", "commit", "-m", f"{proposal.summary}\n\nProposed by SentinelPR Patch Advisor for #{number}.")
    gitutil.git(repo, "push", "--force", "origin", branch)
    body = (f"Proposed fix for #{number}: {proposal.summary}\n\n"
            f"- {len(proposal.tests.passed)} affected test(s) pass with the patch\n"
            f"- static safety review: passed\n\n"
            "SentinelPR analyses this draft like any other pull request. A human must review and approve it.")
    pr = gh.open_draft_pr(branch, base_branch, f"[SentinelPR] {proposal.summary}"[:120], body)
    return f"Opened draft PR #{pr['number']} with a proposed fix: {pr['html_url']}"


def handle(event: dict, cfg: Config, gh: GitHub | None) -> str | None:
    comment = event.get("comment") or {}
    parsed = parse(comment.get("body", ""))
    if parsed is None:
        return None
    if comment.get("author_association") not in ALLOWED:
        return "SentinelPR commands are available to repository members only."
    command, argument = parsed
    issue = event["issue"]
    number = issue["number"]
    is_pr = "pull_request" in issue
    if command == "ask":
        return answer_question(cfg, argument) if argument else "Usage: `/sentinel ask <question>`"
    if not is_pr and command in ("explain", "rerun"):
        return f"`/sentinel {command}` works on pull requests."
    if command == "explain":
        report = latest_run(cfg, number)
        return explain(report) if report else "No SentinelPR run found for this PR yet; try `/sentinel rerun`."
    if command == "rerun":
        report = analyse_pr(cfg, gh.pull(number))
        if gh is not None:
            from sentinel.publish.github import publish

            publish(report, cfg, gh.repo, os.environ["GITHUB_TOKEN"], head_sha=report["pr"]["head_sha"])
        return None  # the refreshed report comment speaks for itself
    # fix
    report = latest_run(cfg, number) if is_pr else None
    problem = f"{issue['title']}\n\n{issue.get('body') or ''}"
    if report:
        failing = "\n".join(f"- {f['nodeid']}: {f.get('message', '')[-300:]}" for f in (report.get("verification") or {}).get("tests", {}).get("failed", []))
        problem += f"\n\nFailing tests on the PR:\n{failing}" if failing else ""
    seeds = ["sym:" + u["id"] for u in (report or {}).get("change_units", []) if u["kind"] != "module"]
    base_branch = event.get("repository", {}).get("default_branch", "main")
    return fix(cfg, gh, number, problem, seeds, report, base_branch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Handle /sentinel commands")
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--dry-run", action="store_true", help="print the reply instead of posting it")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    event = json.loads(args.event.read_text(encoding="utf-8"))
    cfg = Config.load(args.repo_root)
    repository, token = os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GITHUB_TOKEN")
    gh = GitHub(repository, token) if token and repository and not args.dry_run else None
    if gh is not None and event.get("comment", {}).get("id"):
        gh.react(event["comment"]["id"])
    reply = handle(event, cfg, gh)
    if reply:
        if gh is None:
            print(reply)
        else:
            gh.comment(event["issue"]["number"], reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
