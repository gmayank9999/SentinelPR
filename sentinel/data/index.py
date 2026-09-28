"""Build (or refresh) SentinelPR's knowledge of a repository.

    python -m sentinel.data.index                 # history, tracker, SZZ, coverage, retrieval
    python -m sentinel.data.index --no-coverage   # skip the test run
    python -m sentinel.data.index --seed-demo     # (re)build the UniERP demo history first
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from sentinel.config import Config
from sentinel.data.coverage_map import build_coverage_map, project_fingerprint
from sentinel.data.history import mine_commits
from sentinel.data.store import Store
from sentinel.data.szz import run_szz
from sentinel.data.tracker import link_issues_to_prs, load_tracker

log = logging.getLogger("sentinel.index")


def open_store(cfg: Config) -> Store:
    return Store(cfg.workdir / "sentinel.db")


def history_prefix(cfg: Config) -> str:
    """Path prefix of the project inside the repository we mine history from."""
    return cfg.project_prefix if cfg.history_repo.resolve() == cfg.repo_root.resolve() else ""


def index_history(cfg: Config, store: Store) -> dict:
    issues, pulls = load_tracker(cfg.tracker_dir)
    link_issues_to_prs(issues, pulls)
    n_issues, n_prs = store.replace_tracker(issues, pulls)
    labels = {i["number"]: i.get("labels", []) for i in issues}

    commits = mine_commits(
        cfg.history_repo,
        history_prefix(cfg),
        max_commits=cfg.get("project.max_commits"),
        issue_labels=labels or None,
    )
    store.replace_commits(commits)
    links = run_szz(cfg.history_repo, commits, history_prefix(cfg), {i["number"]: i for i in issues})
    store.replace_szz(links)
    return {
        "commits": len(commits),
        "fix_commits": sum(1 for c in commits if c["is_fix"]),
        "issues": n_issues,
        "pulls": n_prs,
        "szz_links": len(links),
    }


def index_coverage(cfg: Config, store: Store, force: bool = False) -> dict:
    package, tests_dir = cfg.get("project.package"), cfg.get("project.tests", "tests")
    fingerprint = project_fingerprint(cfg.project_root, package, tests_dir)
    if not force and store.get_meta("coverage_fingerprint") == fingerprint and store.has_coverage():
        return {"cached": True, "fingerprint": fingerprint}
    rows, tests, summary = build_coverage_map(
        cfg.project_root, package, tests_dir, cfg.workdir, timeout_s=cfg.get("verify.test_timeout_s", 900)
    )
    store.replace_coverage(rows, tests)
    store.set_meta("coverage_fingerprint", fingerprint)
    return {"cached": False, "fingerprint": fingerprint, **summary}


def seed_demo(cfg: Config) -> None:
    seeder = cfg.repo_root / "demo" / "uni-erp" / "history" / "seed_history.py"
    subprocess.run([sys.executable, str(seeder), "--out", str(cfg.history_repo)], check=True)


def build_index(cfg: Config, *, coverage: bool = True, retrieval: bool = True, force: bool = False) -> dict:
    started = time.time()
    store = open_store(cfg)
    summary: dict = {"history": index_history(cfg, store)}
    if coverage:
        summary["coverage"] = index_coverage(cfg, store, force=force)
    if retrieval:
        from sentinel.retrieval.indexer import build_retrieval_index

        summary["retrieval"] = build_retrieval_index(cfg, store)
    summary["elapsed_s"] = round(time.time() - started, 2)
    store.set_meta("last_index", summary)
    store.close()
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the SentinelPR index")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config")
    parser.add_argument("--no-coverage", action="store_true")
    parser.add_argument("--no-retrieval", action="store_true")
    parser.add_argument("--force", action="store_true", help="rebuild the coverage map even if cached")
    parser.add_argument("--seed-demo", action="store_true", help="rebuild the UniERP demo history first")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = Config.load(args.repo_root, args.config)
    if args.seed_demo or (cfg.get("project.history_repo") and not cfg.history_repo.exists()):
        seed_demo(cfg)
    summary = build_index(cfg, coverage=not args.no_coverage, retrieval=not args.no_retrieval, force=args.force)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
