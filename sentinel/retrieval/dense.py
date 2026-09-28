"""Dense (semantic) retrieval: embedders and vector stores.

Embedders
    ``hashing``               feature-hashed sub-word and bigram vectors. No model download,
                              deterministic, surprisingly strong on code identifiers.
    ``st:<model>``            sentence-transformers on CPU, e.g. ``st:BAAI/bge-small-en-v1.5``
                              or ``st:jinaai/jina-embeddings-v2-base-code``.
    ``ollama:<model>``        an Ollama embedding model, e.g. ``ollama:nomic-embed-text``.

Vector stores
    ``numpy``                 exact cosine search over an in-memory matrix, persisted as .npy.
    ``chroma``                ChromaDB persistent collection (used when installed).
    ``faiss``                 FAISS inner-product index, for embedding ablations.
"""

from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path
from typing import Protocol

import numpy as np

from sentinel.retrieval.text import tokenize

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = tokenize(text)
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for feature in features:
            h = zlib.crc32(feature.encode("utf-8"))
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        vec = np.sign(vec) * np.log1p(np.abs(vec))
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self._vector(t) for t in texts])


class SentenceTransformerEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model, device="cpu", trust_remote_code=True)
        self.dim = self.model.get_sentence_embedding_dimension()
        self.name = f"st:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True, batch_size=16), dtype=np.float32)


class OllamaEmbedder:
    def __init__(self, model: str, host: str = "http://localhost:11434"):
        import httpx

        self.client = httpx.Client(base_url=host, timeout=120)
        self.model = model
        self.name = f"ollama:{model}"
        self.dim = len(self._embed_one("probe"))

    def _embed_one(self, text: str) -> list[float]:
        response = self.client.post("/api/embeddings", json={"model": self.model, "prompt": text})
        response.raise_for_status()
        return response.json()["embedding"]

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.asarray([self._embed_one(t) for t in texts], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1, norms)


def make_embedder(spec: str, ollama_host: str = "http://localhost:11434") -> Embedder:
    """Build the configured embedder, degrading to hashing if its backend is unavailable."""
    try:
        if spec.startswith("st:"):
            return SentenceTransformerEmbedder(spec[3:])
        if spec.startswith("ollama:"):
            return OllamaEmbedder(spec[7:], ollama_host)
    except Exception as exc:  # missing package, model download failure, Ollama not running
        log.warning("embedder %s unavailable (%s); falling back to hashing", spec, exc)
    return HashingEmbedder()


class NumpyStore:
    kind = "numpy"

    def __init__(self, ids: list[str] | None = None, matrix: np.ndarray | None = None):
        self.ids = ids or []
        self.matrix = matrix if matrix is not None else np.zeros((0, 0), dtype=np.float32)

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        self.ids.extend(ids)
        self.matrix = vectors if self.matrix.size == 0 else np.vstack([self.matrix, vectors])

    def search(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self.matrix.size == 0:
            return []
        scores = self.matrix @ vector
        top = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in top]

    def save(self, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / f"{name}.npy", self.matrix)
        (directory / f"{name}.ids.json").write_text(json.dumps(self.ids), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path, name: str) -> "NumpyStore | None":
        matrix_file, ids_file = directory / f"{name}.npy", directory / f"{name}.ids.json"
        if not matrix_file.exists() or not ids_file.exists():
            return None
        return cls(json.loads(ids_file.read_text(encoding="utf-8")), np.load(matrix_file))


class ChromaStore:
    kind = "chroma"

    def __init__(self, directory: Path, name: str):
        import chromadb

        self.client = chromadb.PersistentClient(path=str(directory / "chroma"))
        self.collection = self.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def reset(self) -> None:
        name = self.collection.name
        self.client.delete_collection(name)
        self.collection = self.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        for start in range(0, len(ids), 500):
            self.collection.add(ids=ids[start : start + 500], embeddings=vectors[start : start + 500].tolist())

    def search(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        result = self.collection.query(query_embeddings=[vector.tolist()], n_results=k)
        return [(i, 1.0 - float(d)) for i, d in zip(result["ids"][0], result["distances"][0])]


class FaissStore:
    kind = "faiss"

    def __init__(self, dim: int):
        import faiss

        self.index = faiss.IndexFlatIP(dim)
        self.ids: list[str] = []

    def add(self, ids: list[str], vectors: np.ndarray) -> None:
        self.index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        self.ids.extend(ids)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        scores, idx = self.index.search(np.asarray([vector], dtype=np.float32), k)
        return [(self.ids[i], float(s)) for s, i in zip(scores[0], idx[0]) if i >= 0]


class DenseIndex:
    """An embedder plus a vector store, keyed by chunk id."""

    def __init__(self, embedder: Embedder, store):
        self.embedder = embedder
        self.store = store

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        vector = self.embedder.embed([query])[0]
        return self.store.search(vector, k)
