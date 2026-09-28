"""Collect run reports, benchmark results and canary reports into ``dashboard/public/data``.

    python -m sentinel.publish.dashboard_data [--runs 50]

Layout written:
    index.json            what exists (runs, benchmark repos, Q&A, canary reports)
    runs/<run_id>.json    full run reports
    bench/<repo>.json     benchmark results (from bench/out/<repo>/results.json)
    qa.json               Q&A evaluation (bench/out/qa/results.json)
    canary/<name>.json    canary rollout reports (.sentinel/canary/*.json)
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sentinel.config import Config


def collect(cfg: Config, out: Path, max_runs: int = 50) -> dict:
    root = cfg.repo_root
    for sub in ("runs", "bench", "canary"):
        target = out / sub
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)

    reports = []
    for path in sorted((cfg.workdir / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_runs]:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        reports.append(report)
        (out / "runs" / f"{report['run_id']}.json").write_text(json.dumps(report), encoding="utf-8")
    runs = [{"run_id": r["run_id"], "created_at": r["created_at"], "decision": r["decision"], "risk": r["risk"],
             "elapsed_s": r.get("elapsed_s"), "tokens": r.get("tokens", 0), "pr": {k: r["pr"].get(k) for k in ("number", "title", "author")}}
            for r in reports]

    bench = []
    for results in sorted((root / "bench" / "out").glob("*/results.json")):
        if results.parent.name == "qa":
            continue
        bench.append(results.parent.name)
        shutil.copy(results, out / "bench" / f"{results.parent.name}.json")

    qa_file = root / "bench" / "out" / "qa" / "results.json"
    if qa_file.exists():
        shutil.copy(qa_file, out / "qa.json")

    canary = []
    for report in sorted((cfg.workdir / "canary").glob("*.json")):
        canary.append(report.name)
        shutil.copy(report, out / "canary" / report.name)

    index = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runs": runs,
             "bench": bench, "qa": qa_file.exists(), "canary": canary}
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {"runs": len(runs), "bench": bench, "canary": len(canary), "qa": index["qa"]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", type=Path, default=Path("dashboard/public/data"))
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args(argv)
    print(json.dumps(collect(Config.load(args.repo_root), args.out, args.runs), indent=2))


if __name__ == "__main__":
    main()
