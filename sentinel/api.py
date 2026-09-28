"""HTTP API for the dashboard: run reports, the evidence graph, Q&A, benchmark and canary results.

    python -m sentinel.api --port 8765

The dashboard on GitHub Pages works from static JSON; when this API is reachable it adds live
features (the Ask SentinelPR chat and graph exploration around any symbol).
"""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sentinel.config import Config


class Question(BaseModel):
    question: str
    mode: str = "code_history"


def create_api(cfg: Config) -> FastAPI:
    app = FastAPI(title="SentinelPR API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    @lru_cache(maxsize=1)
    def store():
        from sentinel.data.index import open_store

        return open_store(cfg)

    @lru_cache(maxsize=1)
    def retriever():
        from sentinel.retrieval.hybrid import HybridRetriever, RetrievalIndex

        return HybridRetriever(RetrievalIndex.load(cfg, store()))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "project": cfg.get("project.root")}

    @app.get("/api/runs")
    def runs(limit: int = 50):
        return [{k: r.get(k) for k in ("run_id", "created_at", "decision", "risk", "elapsed_s", "tokens")} | {"pr": r.get("pr")}
                for r in store().runs(limit)]

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        for r in store().runs(500):
            if r["run_id"] == run_id:
                return r
        raise HTTPException(404, "unknown run")

    @app.post("/api/ask")
    def ask(body: Question):
        from sentinel.agents.qa import QABot
        from sentinel.llm.client import LLMClient

        answer = QABot(retriever(), LLMClient(cfg)).answer(body.question[:500], mode=body.mode)
        data = answer.to_dict()
        data["contexts"] = [{k: c[k] for k in ("id", "score", "kind")} | {"text": c["text"][:600]} for c in data["contexts"][:8]]
        return data

    @app.get("/api/graph")
    def graph(symbol: str = Query(..., description="path::qualname"), hops: int = 1):
        g = retriever().index.graph
        centre = f"sym:{symbol}"
        if centre not in g.g:
            raise HTTPException(404, "unknown symbol")
        nodes = {centre: 0, **g.neighbourhood([centre], hops)}
        for commit in g.commits_for(centre)[-6:]:
            nodes[commit] = 1
        for bug in g.bug_history(centre):
            nodes[f"commit:{bug['introducing_sha']}"] = 1
        for test in g.covering_tests(centre)[:12]:
            nodes[test] = 1
        edges = [{"source": u, "target": v, "type": d.get("type")} for u, v, d in g.g.edges(data=True)
                 if u in nodes and v in nodes and d.get("type") != "contains"]
        return {"nodes": [{"id": n, "distance": d, **{k: v for k, v in g.node(n).items() if isinstance(v, (str, int, float, bool))}}
                          for n, d in nodes.items()], "edges": edges}

    def _json(path: Path):
        if not path.exists():
            raise HTTPException(404, f"{path.name} not found; run the benchmark first")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/bench/{repo}")
    def bench(repo: str):
        return _json(cfg.repo_root / "bench" / "out" / repo / "results.json")

    @app.get("/api/canary")
    def canary():
        directory = cfg.workdir / "canary"
        return [json.loads(p.read_text()) | {"file": p.name} for p in sorted(directory.glob("*.json"))] if directory.exists() else []

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="SentinelPR API")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    uvicorn.run(create_api(Config.load(args.repo_root)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
