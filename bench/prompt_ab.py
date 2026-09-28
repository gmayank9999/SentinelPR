"""A/B test the Impact prompt versions on benchmark PRs (needs an LLM provider).

    python -m bench.prompt_ab --limit 20

For each labelled PR in ``bench/out/<repo>/prs.jsonl`` the PR is re-created on its base, the
Impact agent's evidence is gathered once, and every prompt version (v1 zero-shot, v2 few-shot,
v3 schema- and evidence-constrained) is asked for claims. Reported per version:

    parse_rate       replies that validate against the claim schema
    grounded_rate    claims citing only evidence ids the model was given
    precision/recall test_impact claims against the execution ground truth
    tokens           prompt + completion tokens
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from bench.metrics import function_level, prf
from bench.run_matrix import ROOT, _apply, _git, _reset, resolve_base
from sentinel.agents.base import PullRequest
from sentinel.agents.impact import ImpactAgent, _describe_changes, _LLMImpact, test_target
from sentinel.agents.prompts import available, load_prompt
from sentinel.config import Config
from sentinel.data.index import open_store
from sentinel.llm.client import LLMClient
from sentinel.orchestrator.pipeline import make_context
from sentinel.retrieval.hybrid import RetrievalIndex


def render(version: int, ctx, ev) -> tuple[str, str]:
    graph = ctx.graph
    tests = [test_target(graph, t) for t in list(ev.line_tests) + list(ev.static_tests)]
    neighbourhood = "\n".join(f"- module {m}: {', '.join(e[:3])}" for m, e in ev.modules.items()) or "(none)"
    return load_prompt("impact", version).render(
        title=ctx.pr.title, body=ctx.pr.body, changes=_describe_changes(ctx), neighbourhood=neighbourhood,
        candidate_tests="\n".join(f"{t}  (evidence: cov:{t})" for t in dict.fromkeys(tests)) or "(none)",
        retrieved="\n\n".join(f"[{r.id}]\n{r.text[:600]}" for r in ev.retrieved[:4]),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="uni-erp")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", type=Path, default=ROOT / "bench" / "out" / "prompt_ab.json")
    args = parser.parse_args(argv)

    repo = ROOT / ".sentinel" / f"{args.repo}-repo"
    labelled = [json.loads(l) for l in (ROOT / "bench" / "out" / args.repo / "prs.jsonl").read_text().splitlines() if l.strip()]
    labelled = [r for r in labelled if r["oracle"]["impacted_tests"] and r["spec"]["category"] != "C7"][: args.limit]
    tree = ROOT / ".sentinel" / "bench" / args.repo / "trees" / "prompt-ab"
    if not tree.exists():
        _git(repo, "worktree", "add", "--detach", "--force", str(tree), "HEAD")

    totals: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    for item in labelled:
        spec, oracle = item["spec"], item["oracle"]
        base_sha, _ = resolve_base(repo, spec["base"])
        _reset(tree, base_sha)
        _apply(tree, "", spec["edits"])
        cfg = Config.load(tree, environ={}, overrides={
            "project": {"root": ".", "package": "unierp", "tests": "tests", "history_repo": str(repo)},
            "workdir": str(ROOT / ".sentinel" / "bench" / args.repo / "index" / base_sha[:10]),
        })
        store = open_store(cfg)
        llm = LLMClient(cfg)
        if not llm.available:
            raise SystemExit("no LLM provider available: set GROQ_API_KEY / GEMINI_API_KEY or start Ollama")
        ctx = make_context(cfg, base=base_sha, head=None, pr=PullRequest(1, spec["title"], spec["body"]),
                           store=store, index=RetrievalIndex.load(cfg, store), llm=llm)
        ev = ImpactAgent().gather(ctx)
        truth = {function_level(t) for t in oracle["impacted_tests"]}
        for version in available()["impact"]:
            system, user = render(version, ctx, ev)
            parsed, calls, _ = llm.structured("impact", system, user, _LLMImpact)
            t = totals[version]
            t["prs"] += 1
            t["tokens"] += sum(c.prompt_tokens + c.completion_tokens for c in calls)
            if parsed is None:
                continue
            t["parsed"] += 1
            claims = parsed.claims
            t["claims"] += len(claims)
            t["grounded"] += sum(1 for c in claims if c.evidence_ids and all(e in ev.ids for e in c.evidence_ids))
            score = prf({function_level(c.target) for c in claims if c.type == "test_impact"}, truth)
            t["precision"] += score["precision"]
            t["recall"] += score["recall"]

    summary = {}
    for version, t in sorted(totals.items()):
        parsed = t["parsed"] or 1
        summary[f"impact@v{version}"] = {
            "prs": int(t["prs"]), "parse_rate": round(t["parsed"] / t["prs"], 3),
            "grounded_rate": round(t["grounded"] / t["claims"], 3) if t["claims"] else None,
            "precision": round(t["precision"] / parsed, 3), "recall": round(t["recall"] / parsed, 3),
            "tokens_per_pr": round(t["tokens"] / t["prs"], 1),
        }
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
