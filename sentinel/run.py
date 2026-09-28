"""Analyse a pull request.

In GitHub Actions::

    python -m sentinel.run --event "$GITHUB_EVENT_PATH" --out .sentinel/run.json --publish

Locally::

    python -m sentinel.run --base main                   # working tree vs main
    python -m sentinel.run --base main --head feature/x  # two revisions (uses a git worktree)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from sentinel import gitutil
from sentinel.agents.base import PullRequest
from sentinel.config import Config
from sentinel.data.index import build_index, open_store, seed_demo
from sentinel.llm.client import LLMClient
from sentinel.orchestrator.pipeline import Pipeline, make_context
from sentinel.retrieval.hybrid import RetrievalIndex

log = logging.getLogger("sentinel")


def worktree(cfg: Config, rev: str, name: str) -> Path:
    path = cfg.workdir / "worktrees" / name
    if path.exists():
        gitutil.git(cfg.repo_root, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        # left behind by an interrupted run and no longer registered with git
        shutil.rmtree(path, ignore_errors=True)
    gitutil.git(cfg.repo_root, "worktree", "prune", check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    gitutil.git(cfg.repo_root, "worktree", "add", "--detach", "--force", str(path), rev)
    return path


def config_for(cfg: Config, root: Path) -> Config:
    """The same configuration applied to another checkout (a worktree) of the repository.
    Paths that must stay shared (workdir, separate history repo, tracker data) are pinned."""
    overrides: dict = {"workdir": str(cfg.workdir)}
    project: dict = {}
    if cfg.history_repo.resolve() != cfg.repo_root.resolve():
        project["history_repo"] = str(cfg.history_repo)
    if cfg.tracker_dir:
        project["tracker_dir"] = str(cfg.tracker_dir)
    if project:
        overrides["project"] = project
    return Config.load(root, overrides=cfg.with_overrides(overrides).data)


def ensure_index(cfg: Config, base_sha: str, force: bool = False):
    """Index the *base* revision (coverage line numbers must refer to the base), cached by SHA."""
    store = open_store(cfg)
    if not force and store.get_meta("indexed_revision") == base_sha and store.has_coverage():
        return store
    store.close()
    if cfg.get("project.history_repo") and not cfg.history_repo.exists():
        seed_demo(cfg)
    log.info("indexing base revision %s", base_sha[:10])
    tree = worktree(cfg, base_sha, f"base-{base_sha[:10]}")
    try:
        build_index(config_for(cfg, tree), force=True)
    finally:
        gitutil.git(cfg.repo_root, "worktree", "remove", "--force", str(tree), check=False)
    store = open_store(cfg)
    store.set_meta("indexed_revision", base_sha)
    return store


def pr_from_event(path: Path) -> tuple[PullRequest, str]:
    event = json.loads(path.read_text(encoding="utf-8"))
    pr = event["pull_request"]
    head_repo = (pr.get("head", {}).get("repo") or {}).get("full_name")
    base_repo = (pr.get("base", {}).get("repo") or {}).get("full_name")
    return PullRequest(
        number=pr["number"],
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        author=pr.get("user", {}).get("login", ""),
        base_sha=pr["base"]["sha"],
        head_sha=pr["head"]["sha"],
        labels=[label["name"] for label in pr.get("labels", [])],
        is_fork=bool(head_repo and base_repo and head_repo != base_repo),
    ), event.get("repository", {}).get("full_name", "")


def analyse(cfg: Config, *, base: str, head: str | None, pr: PullRequest, impact_mode: str | None = None, force_index: bool = False) -> dict:
    base_sha = gitutil.rev_parse(cfg.repo_root, base)
    pr.base_sha = base_sha
    run_cfg, run_head = cfg, None
    tree = None
    if head is not None:
        head_sha = gitutil.rev_parse(cfg.repo_root, head)
        pr.head_sha = pr.head_sha or head_sha
        if head_sha != gitutil.rev_parse(cfg.repo_root, "HEAD"):
            tree = worktree(cfg, head_sha, f"head-{head_sha[:10]}")
            run_cfg = config_for(cfg, tree)
        else:
            run_head = head_sha
    else:
        pr.head_sha = pr.head_sha or gitutil.rev_parse(cfg.repo_root, "HEAD")
    if not pr.commit_messages:
        log_range = f"{base_sha}..{pr.head_sha}"
        raw = gitutil.git(cfg.repo_root, "log", "--format=%B%x1e", log_range, check=False)
        pr.commit_messages = [m.strip() for m in raw.split("\x1e") if m.strip()][:50]
    if not pr.author:
        pr.author = gitutil.git(cfg.repo_root, "log", "-1", "--format=%an", pr.head_sha, check=False).strip()

    try:
        store = ensure_index(cfg, base_sha, force=force_index)
        index = RetrievalIndex.load(run_cfg, store)
        llm = LLMClient(run_cfg)
        ctx = make_context(run_cfg, base=base_sha, head=run_head, pr=pr, store=store, index=index, llm=llm)
        report = Pipeline(run_cfg, ctx, impact_mode=impact_mode).run()
        store.save_run(report)
        return report
    finally:
        if tree is not None:
            gitutil.git(cfg.repo_root, "worktree", "remove", "--force", str(tree), check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SentinelPR: evidence-gated analysis of a pull request")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config")
    parser.add_argument("--event", type=Path, help="GitHub pull_request event payload")
    parser.add_argument("--base", help="base revision (local mode)")
    parser.add_argument("--head", help="head revision; defaults to the working tree")
    parser.add_argument("--local", action="store_true", help="accepted for readability; implied by --base")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--publish", action="store_true", help="post the Check Run and PR comment")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--impact-mode", choices=["auto", "llm", "rules", "static", "lexical"])
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--fail-on-block", action="store_true", help="exit 1 when the decision is BLOCK")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    overrides = {"llm": {"enabled": False}} if args.no_llm else None
    cfg = Config.load(args.repo_root, args.config, overrides=overrides)
    repository = os.getenv("GITHUB_REPOSITORY", "")
    if args.event:
        pr, repository = pr_from_event(args.event)
        base, head = pr.base_sha, None  # Actions checks out the PR merge commit
    elif args.base:
        pr = PullRequest(number=args.pr, title=args.title, body=args.body)
        base, head = args.base, args.head
    else:
        parser.error("either --event or --base is required")
    cfg = cfg.with_overrides({"publish": {"repository": repository}})

    report = analyse(cfg, base=base, head=head, pr=pr, impact_mode=args.impact_mode, force_index=args.reindex)
    runs_dir = cfg.workdir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, default=str)
    (runs_dir / f"{report['run_id']}.json").write_text(payload, encoding="utf-8")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")

    if args.publish:
        token = os.getenv("GITHUB_TOKEN")
        if token and repository and pr.head_sha:
            from sentinel.publish.github import publish

            result = publish(report, cfg, repository, token, head_sha=pr.head_sha)
            log.info("published: %s", result)
        else:
            log.warning("--publish given but GITHUB_TOKEN / repository / head SHA missing; skipped")

    risk = "n/a" if report["risk"] is None else f"{report['risk']:.2f}"
    print(f"SentinelPR decision: {report['decision']} (risk {risk}): {report['decision_reason']}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        from sentinel.publish.render import render_comment

        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(render_comment(report, "", cfg.get("publish.dashboard_url")) + "\n")
    return 1 if args.fail_on_block and report["decision"] == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())
