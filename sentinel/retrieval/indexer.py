"""Build and load the retrieval index: code chunks, history documents, graph and embeddings."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from sentinel.config import Config
from sentinel.retrieval.chunker import Chunk, chunk_module, parse_python
from sentinel.retrieval.dense import ChromaStore, NumpyStore, make_embedder
from sentinel.retrieval.graph import CodeGraph

log = logging.getLogger(__name__)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv", "venv"}


def project_sources(cfg: Config) -> dict[str, str]:
    """Every Python file in the project (package, tests, scripts), keyed by project-relative path."""
    root = cfg.project_root
    excluded = set(cfg.get("project.exclude") or [])
    sources = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or (rel.parts and rel.parts[0] in excluded):
            continue
        sources[rel.as_posix()] = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    return sources


def code_chunks(sources: dict[str, str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, text in sources.items():
        chunks.extend(chunk_module(text, path, parse_python(text, path)))
    return chunks


def history_documents(store) -> list[dict]:
    """One document per commit, issue thread and pull request discussion."""
    docs = []
    for c in store.commits():
        functions = ", ".join(u.split("::", 1)[1] for u in c["functions"][:12])
        files = ", ".join(f["path"] for f in c["files"][:12])
        docs.append(
            {
                "id": f"commit:{c['sha'][:10]}",
                "kind": "commit",
                "ref": c["sha"],
                "title": c["message"].splitlines()[0] if c["message"] else "",
                "text": f"commit {c['sha'][:10]} by {c['author']}\n{c['message']}\nfiles: {files}\nfunctions: {functions}",
                "timestamp": c["timestamp"],
            }
        )
    for issue in store.issues():
        thread = "\n".join(f"{x['author']}: {x['body']}" for x in issue["comments"])
        docs.append(
            {
                "id": f"issue:{issue['number']}",
                "kind": "issue",
                "ref": issue["number"],
                "title": issue["title"],
                "text": f"issue #{issue['number']} [{', '.join(issue['labels'])}] {issue['title']}\n{issue['body']}\n{thread}",
            }
        )
    for pr in store.prs():
        reviews = "\n".join(f"{x['author']}: {x['body']}" for x in pr["reviews"])
        docs.append(
            {
                "id": f"pr:{pr['number']}",
                "kind": "pr",
                "ref": pr["number"],
                "title": pr["title"],
                "text": f"pull request #{pr['number']} {pr['title']}\n{pr['body']}\n{reviews}",
            }
        )
    return docs


def retrieval_dir(cfg: Config) -> Path:
    return cfg.workdir / "retrieval"


def _vector_store(cfg: Config, directory: Path, name: str):
    kind = cfg.get("retrieval.vector_store", "auto")
    if kind in ("auto", "chroma"):
        try:
            store = ChromaStore(directory, name)
            store.reset()
            return store
        except Exception as exc:
            if kind == "chroma":
                raise
            log.info("chromadb not available (%s); using the numpy vector store", exc)
    return NumpyStore()


def build_retrieval_index(cfg: Config, store) -> dict:
    directory = retrieval_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    sources = project_sources(cfg)
    chunks = code_chunks(sources)
    docs = history_documents(store)

    graph = CodeGraph.build(sources, store, [t["test_id"] for t in store.all_tests()])
    graph.save(directory / "graph.json")

    embedder = make_embedder(cfg.get("retrieval.embedder", "hashing"), cfg.get("llm.providers.ollama.host"))
    stores = {}
    for name, ids, texts in (
        ("code", [c.id for c in chunks], [c.text for c in chunks]),
        ("history", [d["id"] for d in docs], [d["text"] for d in docs]),
    ):
        vectors = embedder.embed(texts)
        vs = _vector_store(cfg, directory, name)
        if ids:
            vs.add(ids, vectors)
        if isinstance(vs, NumpyStore):
            vs.save(directory, name)
        stores[name] = vs.kind

    (directory / "chunks.json").write_text(json.dumps([asdict(c) for c in chunks]), encoding="utf-8")
    (directory / "history.json").write_text(json.dumps(docs), encoding="utf-8")
    meta = {"embedder": embedder.name, "dim": embedder.dim, "vector_stores": stores, "chunks": len(chunks), "history_docs": len(docs)}
    (directory / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {**meta, "graph": graph.stats()}
