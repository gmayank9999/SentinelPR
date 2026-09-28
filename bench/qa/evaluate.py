"""Evaluate the Q&A bot on the question set (RQ4: no-RAG vs code-RAG vs code+history-RAG).

    python -m bench.qa.evaluate                 # extractive answers (no LLM needed)
    python -m bench.qa.evaluate --llm           # LLM answers for all three conditions

Metrics per condition and category:
    accuracy            answerable: cites at least one gold source; unanswerable/adversarial: refuses
    citation_precision  share of cited ids that match a gold source
    hallucination_rate  share of unanswerable/adversarial questions that were answered anyway
    latency             seconds per question
With ``ragas`` installed and an LLM configured, RAGAS faithfulness and context precision/recall
are added.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from sentinel.agents.qa import QABot
from sentinel.config import Config
from sentinel.data.index import open_store
from sentinel.llm.client import LLMClient
from sentinel.retrieval.hybrid import HybridRetriever, RetrievalIndex

HERE = Path(__file__).resolve().parent
REFUSAL_CATEGORIES = {"unanswerable", "adversarial"}


MERGE_COMMITS: dict[str, str] = {}  # "pr:43" -> "commit:<merge sha[:10]>", filled from the store


def matches(citation: str, gold: str) -> bool:
    if gold == "commit":
        return citation.startswith("commit:")
    # a pull request and the commit it was merged as are the same piece of history
    return citation == gold or citation.startswith(gold) or citation == MERGE_COMMITS.get(gold)


def score(item: dict, answer) -> dict:
    refusal_expected = item["category"] in REFUSAL_CATEGORIES
    cited = answer.citations
    hits = [c for c in cited if any(matches(c, g) for g in item["gold"])]
    if refusal_expected:
        correct = answer.refused
    else:
        correct = (not answer.refused) and bool(hits)
    return {
        "correct": correct,
        "citation_precision": (len(hits) / len(cited)) if cited else None,
        "hallucinated": refusal_expected and not answer.refused,
        "latency_s": answer.latency_s,
        "tokens": answer.tokens,
    }


def evaluate(bot: QABot, questions: list[dict], mode: str, use_llm: bool) -> dict:
    rows = []
    for item in questions:
        answer = bot.answer(item["question"], mode=mode, use_llm=use_llm)
        rows.append({"id": item["id"], "category": item["category"], "answer": answer.answer, "citations": answer.citations,
                     "refused": answer.refused, **score(item, answer)})
    by_category = defaultdict(list)
    for r in rows:
        by_category[r["category"]].append(r)

    def summary(items: list[dict]) -> dict:
        precision = [r["citation_precision"] for r in items if r["citation_precision"] is not None]
        refusal_items = [r for r in items if r["category"] in REFUSAL_CATEGORIES]
        return {
            "n": len(items),
            "accuracy": round(float(np.mean([r["correct"] for r in items])), 4),
            "citation_precision": round(float(np.mean(precision)), 4) if precision else None,
            "hallucination_rate": round(float(np.mean([r["hallucinated"] for r in refusal_items])), 4) if refusal_items else None,
            "latency_s": round(float(np.mean([r["latency_s"] for r in items])), 3),
        }

    return {"overall": summary(rows), "by_category": {k: summary(v) for k, v in sorted(by_category.items())}, "answers": rows}


def ragas_scores(bot: QABot, questions: list[dict]) -> dict | None:
    try:
        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import context_precision, context_recall, faithfulness
    except ImportError:
        return None
    records = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for item in questions:
        if item["category"] in REFUSAL_CATEGORIES:
            continue
        answer = bot.answer(item["question"], mode="code_history")
        records["question"].append(item["question"])
        records["answer"].append(answer.answer)
        records["contexts"].append([c["text"] for c in answer.contexts] or [""])
        records["ground_truth"].append(" ".join(item["keywords"]))
    try:
        result = ragas_evaluate(Dataset.from_dict(records), metrics=[faithfulness, context_precision, context_recall])
        return {k: round(float(v), 4) for k, v in result.items()}
    except Exception as exc:  # RAGAS needs its own LLM configuration
        return {"error": str(exc)[:200]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("bench/out/qa/results.json"))
    args = parser.parse_args(argv)
    cfg = Config.load(args.repo_root)
    store = open_store(cfg)
    MERGE_COMMITS.update({f"pr:{p['number']}": f"commit:{p['merge_sha'][:10]}" for p in store.prs() if p.get("merge_sha")})
    bot = QABot(HybridRetriever(RetrievalIndex.load(cfg, store)), LLMClient(cfg) if args.llm else None)
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    modes = ("no_rag", "code", "code_history") if args.llm else ("code", "code_history")
    results = {"engine": "llm" if args.llm else "extractive", "questions": len(questions),
               "conditions": {mode: evaluate(bot, questions, mode, args.llm) for mode in modes}}
    if args.llm:
        results["ragas"] = ragas_scores(bot, questions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    for mode, r in results["conditions"].items():
        o = r["overall"]
        print(f"{mode:13} accuracy {o['accuracy']:.2f}  citation precision {o['citation_precision']}  hallucination {o['hallucination_rate']}")
        for cat, c in r["by_category"].items():
            print(f"    {cat:14} {c['accuracy']:.2f} (n={c['n']})")


if __name__ == "__main__":
    main()
