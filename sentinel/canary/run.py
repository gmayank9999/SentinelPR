"""Run a canary rollout of UniERP locally (no Docker needed).

    # canary = working tree with an injected slow endpoint; expect an automatic rollback
    python -m sentinel.canary.run --canary-env UNIERP_FAULT=slow --canary-env UNIERP_FAULT_RATE=0.3

    # stable = the previous commit, canary = HEAD
    python -m sentinel.canary.run --stable-ref HEAD~1 --canary-ref HEAD

Stable and canary run as separate uvicorn processes; a weighted proxy sits in front of them,
synthetic users hit the proxy, and the controller walks the rollout steps.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

from sentinel import gitutil
from sentinel.canary.controller import RolloutController
from sentinel.canary.monitor import SLO
from sentinel.canary.proxy import create_proxy

log = logging.getLogger("sentinel.canary")
APP = "unierp.api.app:app"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(url: str, timeout_s: float = 30) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy")


@contextmanager
def service(directory: Path, version: str, env: dict[str, str]):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", APP, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=directory, env={**os.environ, "UNIERP_VERSION": version, "PYTHONDONTWRITEBYTECODE": "1", **env},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(url)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextmanager
def proxy(stable_url: str, canary_url: str):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_proxy(stable_url, canary_url), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    wait_healthy(url)  # forwarded to stable
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextmanager
def checkout(repo: Path, ref: str | None, subdir: str, scratch: Path, name: str):
    """A directory with the project at ``ref`` (or the working tree when ref is None)."""
    if ref is None:
        yield repo / subdir
        return
    tree = scratch / name
    gitutil.git(repo, "worktree", "remove", "--force", str(tree), check=False)
    gitutil.git(repo, "worktree", "prune", check=False)
    gitutil.git(repo, "worktree", "add", "--detach", "--force", str(tree), gitutil.rev_parse(repo, ref))
    try:
        yield tree / subdir
    finally:
        gitutil.git(repo, "worktree", "remove", "--force", str(tree), check=False)


def parse_env(items: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canary rollout with automatic rollback")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--app-dir", default="demo/uni-erp")
    parser.add_argument("--stable-ref")
    parser.add_argument("--canary-ref")
    parser.add_argument("--stable-env", action="append", default=[])
    parser.add_argument("--canary-env", action="append", default=[])
    parser.add_argument("--step-seconds", type=float, default=15)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--rate", type=float, default=12, help="requests per second per user")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--p95-ratio", type=float, default=1.5)
    parser.add_argument("--label", action="append", default=[], help="key=value recorded in the report (e.g. scenario=slow)")
    parser.add_argument("--out", type=Path, default=Path(".sentinel/canary/latest.json"))
    parser.add_argument("--fail-on-rollback", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    repo = args.repo_root.resolve()
    scratch = repo / ".sentinel" / "canary" / "trees"
    scratch.mkdir(parents=True, exist_ok=True)
    slo = SLO(max_error_rate=args.max_error_rate, p95_ratio=args.p95_ratio)
    with checkout(repo, args.stable_ref, args.app_dir, scratch, "stable") as stable_dir, \
            checkout(repo, args.canary_ref, args.app_dir, scratch, "canary") as canary_dir, \
            service(stable_dir, args.stable_ref or "stable", parse_env(args.stable_env)) as stable_url, \
            service(canary_dir, args.canary_ref or "canary", parse_env(args.canary_env)) as canary_url, \
            proxy(stable_url, canary_url) as proxy_url:
        controller = RolloutController(proxy_url, stable_url, canary_url, step_seconds=args.step_seconds, slo=slo,
                                       users=args.users, rate_per_user=args.rate)
        report = controller.run()

    report.labels = {"stable": args.stable_ref or "working tree", "canary": args.canary_ref or "working tree",
                     "canary_env": parse_env(args.canary_env), **parse_env(args.label)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"canary {report.outcome}: {report.canary_requests}/{report.total_requests} requests served by the canary "
          f"({report.exposed_share:.1%})" + (f", breach detected after {report.time_to_detection_s}s" if report.time_to_detection_s else ""))
    return 1 if args.fail_on_rollback and report.outcome == "rolled_back" else 0


if __name__ == "__main__":
    sys.exit(main())
