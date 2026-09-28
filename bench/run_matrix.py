"""Run the benchmark: generate PRs, label them by execution, analyse them with SentinelPR.

    python -m bench.run_matrix --repo uni-erp --workers 4
    python -m bench.run_matrix --repo uni-erp --bases "Release 1.4.0" --limit 10   # quick run

Phase 1 (per base revision): generate candidate PRs of every category and label each one
with the execution oracle. Phase 2: run the full SentinelPR pipeline — plus the lexical and
static impact baselines — on the selected PRs. Both phases run in worker processes, each with
its own git worktree. Output: ``prs.jsonl`` (labelled specs) and ``results.jsonl`` (one row
per analysed PR) under ``bench/out/<repo>/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.generate import adversarial, api, config as config_gen, faults, noise, refactor, regression  # noqa: E402
from bench.generate.base import PRSpec, Snapshot  # noqa: E402
from bench.metrics import claim_accuracy, function_level, impact_scores  # noqa: E402
from bench.oracle import Oracle  # noqa: E402

log = logging.getLogger("bench")

# --- worker state (one per process) ------------------------------------------------------
_WORKER: dict = {}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _worker_tree(job: dict) -> Path:
    key = (job["repo_path"], os.getpid())
    tree = _WORKER.get(key)
    if tree is None:
        tree = Path(job["work_root"]) / "trees" / f"w{os.getpid()}"
        if tree.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=job["repo_path"], capture_output=True)
        if tree.exists():
            shutil.rmtree(tree, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=job["repo_path"], capture_output=True)
        tree.parent.mkdir(parents=True, exist_ok=True)
        _git(Path(job["repo_path"]), "worktree", "add", "--detach", "--force", str(tree), job["base_sha"])
        _WORKER[key] = tree
    return tree


def _reset(tree: Path, base_sha: str) -> None:
    _git(tree, "checkout", "-q", "--force", "--detach", base_sha)
    _git(tree, "clean", "-q", "-fdx")


def _apply(tree: Path, prefix: str, edits: dict[str, str | None]) -> None:
    for rel, content in edits.items():
        target = tree / (prefix + rel)
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")


def _changed_lines(tree: Path, prefix: str, base_sha: str) -> dict[str, set[int]]:
    from sentinel.retrieval.diff_parser import DiffContext
    from sentinel.verify.verifier import changed_source_lines

    ctx = DiffContext(tree, base_sha, None, prefix)
    changes = ctx.file_changes()
    units = ctx.change_units(changes)
    sources = {c.path: ctx.read_new(c.path) or "" for c in changes if c.path.endswith(".py")}
    return changed_source_lines(units, sources)


def _oracle(job: dict) -> Oracle:
    key = ("oracle", job["base_sha"], os.getpid())
    oracle = _WORKER.get(key)
    if oracle is None:
        tree = _worker_tree(job)
        _reset(tree, job["base_sha"])
        oracle = Oracle(job["base_tests"], Path(job["hidden"]) if job.get("hidden") else None, job["package"], job["tests"])
        oracle.baseline(tree / job["prefix"] if job["prefix"] else tree, Path(job["work_root"]) / "scratch" / f"w{os.getpid()}" / "baseline")
        _WORKER[key] = oracle
    return oracle


def label_job(job: dict) -> dict:
    """Phase 1: apply the PR on the base and label it by execution."""
    spec = PRSpec.from_dict(job["spec"])
    tree = _worker_tree(job)
    oracle = _oracle(job)
    _reset(tree, job["base_sha"])
    _apply(tree, job["prefix"], spec.edits)
    project = tree / job["prefix"] if job["prefix"] else tree
    changed = _changed_lines(tree, job["prefix"], job["base_sha"])
    result = oracle.evaluate(project, changed, Path(job["work_root"]) / "scratch" / f"w{os.getpid()}")
    return {"spec": spec.to_dict(), "oracle": result.__dict__}


def analyse_job(job: dict) -> dict:
    """Phase 2: run SentinelPR (and the impact baselines) on one labelled PR."""
    from sentinel.agents.base import PullRequest
    from sentinel.agents.impact import ImpactAgent
    from sentinel.config import Config
    from sentinel.data.index import open_store
    from sentinel.llm.client import LLMClient
    from sentinel.orchestrator.pipeline import Pipeline, make_context
    from sentinel.retrieval.hybrid import RetrievalIndex

    spec = PRSpec.from_dict(job["spec"])
    oracle = job["oracle"]
    tree = _worker_tree(job)
    _reset(tree, job["base_sha"])
    _apply(tree, job["prefix"], spec.edits)
    scratch = Path(job["work_root"]) / "scratch" / f"w{os.getpid()}" / "verify"
    cfg = Config.load(tree, environ=job["environ"], overrides={
        "project": {"root": job["project_root"], "package": job["package"], "tests": job["tests"],
                    "history_repo": job["repo_path"], "tracker_dir": job.get("tracker_dir")},
        "workdir": job["index_dir"],
        "verify": {"scratch": str(scratch)},
        "llm": {"enabled": job["llm"]},
    })
    started = time.time()
    store = open_store(cfg)
    index = RetrievalIndex.load(cfg, store)
    llm = LLMClient(cfg)
    pr = PullRequest(number=job["number"], title=spec.title, body=spec.body, author="bench-author",
                     base_sha=job["base_sha"], head_sha=None, commit_messages=[spec.title])
    ctx = make_context(cfg, base=job["base_sha"], head=None, pr=pr, store=store, index=index, llm=llm)
    report = Pipeline(cfg, ctx).run()

    lexical = ImpactAgent("lexical").run(ctx).claims
    static = ImpactAgent("static").run(ctx).claims
    gt_tests, gt_modules = oracle["impacted_tests"], oracle["impacted_modules"]
    claims = report["claims"]
    impact_claims = [c for c in claims if c["agent"] == "impact"]
    selection = report.get("selection") or {}
    selected = {function_level(t) for t in selection.get("tests", [])}
    failing = {function_level(t) for t in oracle["visible_failing"]}
    verification = report.get("verification") or {}
    row = {
        "id": spec.id, "repo": spec.repo, "base": spec.base, "base_order": job["base_order"], "created_at": job["created_at"],
        "category": spec.category, "generator": spec.generator, "attack": spec.attack, "title": spec.title,
        "label": oracle["label"],
        "oracle": {k: oracle[k] for k in ("newly_failing", "visible_failing", "impacted_tests", "impacted_modules", "changed_lines")},
        "tests_pass": int(not oracle["visible_failing"] and not oracle["visible_collection_error"]),
        "sentinel": {
            "decision": report["decision"], "risk": report["risk"], "rejected": report["rejected"],
            "impact_mode": report["impact_mode"], "verdict_counts": report["verdict_counts"],
            "tokens": report["tokens"], "llm_calls": report["llm"]["calls"], "elapsed_s": round(time.time() - started, 2),
            "guards": sorted({e["guard"] for e in report["guard_events"] if e["severity"] != "info"}),
            "mutation_score": (verification.get("mutation") or {}).get("score"),
            "changed_coverage": (verification.get("changed_lines") or {}).get("coverage"),
            "selected": len(selection.get("tests", [])), "suite": selection.get("total_available", 0),
            "time_saved": selection.get("time_saved_ratio", 0.0),
            "safe_selection": (len(selected & failing) / len(failing)) if failing else None,
            "trace": [{"node": t["node"], "s": round(t["end"] - t["start"], 3), "tokens": t["tokens"]} for t in report["trace"]],
        },
        "features": report["features"],
        "impact": {
            "sentinel": impact_scores(impact_claims, gt_tests, gt_modules),
            "sentinel_verified": impact_scores(impact_claims, gt_tests, gt_modules, verified_only=True),
            "lexical": impact_scores([c.model_dump() for c in lexical], gt_tests, gt_modules),
            "static": impact_scores([c.model_dump() for c in static], gt_tests, gt_modules),
        },
        "claims": claim_accuracy(impact_claims, gt_tests, gt_modules),
        "history_claims": {s: sum(1 for c in claims if c["agent"] == "historian" and c["status"] == s) for s in ("VERIFIED", "REFUTED", "UNVERIFIABLE")},
    }
    if job["llm"] and llm.available:
        from bench.baselines import llm_reviewer

        row["llm_reviewer"] = llm_reviewer(llm, spec, report)
    return row


# --- orchestration --------------------------------------------------------------------------

def resolve_base(repo: Path, ref: str) -> tuple[str, int]:
    try:
        sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        sha = _git(repo, "log", "--format=%H", "-1", "--fixed-strings", f"--grep={ref}")
    if not sha:
        raise SystemExit(f"cannot resolve base {ref!r} in {repo}")
    return sha, int(_git(repo, "log", "-1", "--format=%ct", sha))


def ensure_repo(name: str, spec: dict) -> Path:
    path = ROOT / spec["path"]
    if spec.get("seed"):
        if not path.exists() or not (path / ".git").exists():
            subprocess.run([sys.executable, str(ROOT / spec["seed"]), "--out", str(path)], check=True)
    elif not path.exists():
        subprocess.run(["git", "clone", "--quiet", spec["url"], str(path)], check=True)
    return path


def index_base(repo: Path, spec: dict, base_sha: str, index_dir: Path) -> None:
    from sentinel.config import Config
    from sentinel.run import ensure_index

    cfg = Config.load(repo, environ={}, overrides={
        "project": {"root": spec["project_root"], "package": spec["package"], "tests": spec["tests"],
                    "tracker_dir": str(ROOT / spec["tracker_dir"]) if spec.get("tracker_dir") else None},
        "workdir": str(index_dir),
    })
    ensure_index(cfg, base_sha).close()


def run(repo_name: str, *, bases: list[str] | None, workers: int, limit: int | None, out: Path, llm: bool, resume: bool = False) -> None:
    repos = yaml.safe_load((ROOT / "bench" / "repos.yaml").read_text())["repos"]
    spec = repos[repo_name]
    repo = ensure_repo(repo_name, spec)
    prefix = "" if spec["project_root"] in (".", "") else spec["project_root"].rstrip("/") + "/"
    work_root = ROOT / ".sentinel" / "bench" / repo_name
    out.mkdir(parents=True, exist_ok=True)
    counts = spec["per_base"]
    environ = {k: v for k, v in os.environ.items() if k in ("GROQ_API_KEY", "GEMINI_API_KEY")} if llm else {}
    labelled_all, rows = [], []
    done_bases: set[str] = set()
    if resume and (out / "results.jsonl").exists():
        rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        labelled_all = [json.loads(l) for l in (out / "prs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        done_bases = {r["base"] for r in rows}
        log.info("resuming: %d PRs already analysed on %s", len(rows), ", ".join(sorted(done_bases)) or "no bases")

    for order, base_ref in enumerate(bases or spec["bases"]):
        if base_ref in done_bases:
            continue
        base_sha, base_time = resolve_base(repo, base_ref)
        index_dir = work_root / "index" / base_sha[:10]
        log.info("[%s] base %s (%s): indexing", repo_name, base_ref, base_sha[:10])
        index_base(repo, spec, base_sha, index_dir)

        snapshot = Snapshot(repo, base_sha, prefix, spec["package"].replace("src/", ""), spec["tests"])
        seed = f"{repo_name}:{base_sha[:10]}"
        candidates = (
            refactor.generate(snapshot, counts["C1"], seed) + faults.generate(snapshot, counts["FAULT"], seed)
            + api.generate(snapshot, counts["C3"], seed) + regression.generate(snapshot, counts["C4"], seed)
            + config_gen.generate(snapshot, counts["C5"], seed) + noise.generate(snapshot, counts["C6"], seed)
        )
        for i, c in enumerate(candidates):
            c.id, c.repo, c.base = f"{repo_name}-{base_sha[:7]}-{i:03d}", repo_name, base_ref
        if limit:
            candidates = candidates[:limit]
        common = {
            "repo_path": str(repo), "work_root": str(work_root), "base_sha": base_sha, "prefix": prefix,
            "project_root": spec["project_root"], "package": spec["package"].replace("src/", ""), "tests": spec["tests"],
            "hidden": str(ROOT / spec["hidden"]) if spec.get("hidden") else None,
            "tracker_dir": str(ROOT / spec["tracker_dir"]) if spec.get("tracker_dir") else None,
            "index_dir": str(index_dir), "llm": llm, "environ": environ,
            "base_tests": {p: snapshot.read(p) for p in snapshot.files if p.startswith(spec["tests"] + "/") and p.endswith(".py")},
        }

        log.info("[%s] %s: labelling %d candidate PRs", repo_name, base_ref, len(candidates))
        labelled = _parallel(label_job, [{**common, "spec": c.to_dict()} for c in candidates], workers)
        chosen = _select(labelled, counts)
        defective = [PRSpec.from_dict(r["spec"]) for r in chosen if r["oracle"]["label"] == 1 and r["spec"]["category"] in ("C2", "HF", "C4")]
        by_id = {r["spec"]["id"]: r for r in chosen}
        for attacked in adversarial.generate(defective, counts["C7"], seed):
            attacked.id = attacked.id + "-c7"
            chosen.append({"spec": attacked.to_dict(), "oracle": by_id[attacked.id[:-3]]["oracle"]})
        labelled_all += chosen

        log.info("[%s] %s: analysing %d PRs", repo_name, base_ref, len(chosen))
        jobs = [{**common, "spec": r["spec"], "oracle": r["oracle"], "number": 1000 * order + i, "base_order": order,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(base_time + 60 * i))}
                for i, r in enumerate(chosen)]
        rows += _parallel(analyse_job, jobs, workers)
        _write(out, labelled_all, rows)
    log.info("done: %d PRs analysed -> %s", len(rows), out)


def _select(labelled: list[dict], counts: dict) -> list[dict]:
    """Assign final categories (fault -> C2 or HF) and cap each category."""
    chosen, taken = [], {}
    for r in labelled:
        spec, oracle = r["spec"], r["oracle"]
        category = spec["category"]
        if category == "FAULT":
            if oracle["label"] == 0:
                continue  # neither the visible suite nor the hidden oracle notices: possibly equivalent
            category = "C2" if oracle["visible_failing"] or oracle["visible_collection_error"] else "HF"
            spec["category"] = category
        if taken.get(category, 0) >= counts.get(category, 99):
            continue
        taken[category] = taken.get(category, 0) + 1
        chosen.append(r)
    return chosen


def _parallel(fn, jobs: list[dict], workers: int) -> list[dict]:
    results = []
    if workers <= 1:
        for job in jobs:
            results.append(_safe(fn, job))
        return [r for r in results if r]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_safe, fn, job) for job in jobs]
        for done, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result:
                results.append(result)
            if done % 10 == 0:
                log.info("  %d/%d", done, len(jobs))
    order = {job["spec"]["id"]: i for i, job in enumerate(jobs)}
    return sorted(results, key=lambda r: order.get(r.get("id") or r["spec"]["id"], 0))


def _safe(fn, job: dict) -> dict | None:
    try:
        return fn(job)
    except Exception as exc:  # one broken PR must not sink the run
        logging.getLogger("bench").exception("job %s failed: %s", job["spec"]["id"], exc)
        return None


def _write(out: Path, labelled: list[dict], rows: list[dict]) -> None:
    (out / "prs.jsonl").write_text("\n".join(json.dumps(r) for r in labelled) + "\n", encoding="utf-8")
    (out / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="uni-erp")
    parser.add_argument("--bases", nargs="*")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--limit", type=int, help="cap candidates per base (smoke runs)")
    parser.add_argument("--llm", action="store_true", help="use configured LLM providers")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--resume", action="store_true", help="keep results for bases already completed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("sentinel").setLevel(logging.WARNING)
    run(args.repo, bases=args.bases, workers=args.workers, limit=args.limit, out=args.out or ROOT / "bench" / "out" / args.repo, llm=args.llm, resume=args.resume)


if __name__ == "__main__":
    main()
