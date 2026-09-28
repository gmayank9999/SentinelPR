import numpy as np
import pytest

from sentinel.config import Config
from sentinel.retrieval.bm25 import BM25Index
from sentinel.retrieval.dense import DenseIndex, HashingEmbedder, NumpyStore
from sentinel.retrieval.graph import CodeGraph, module_to_path
from sentinel.retrieval.hybrid import HybridRetriever, RetrievalIndex
from sentinel.retrieval.indexer import code_chunks
from sentinel.retrieval.lexical import LocalCodeSearch
from sentinel.retrieval.rerank import TfidfReranker
from sentinel.retrieval.rrf import reciprocal_rank_fusion
from sentinel.retrieval.text import tokenize

SOURCES = {
    "app/__init__.py": "",
    "app/credits.py": (
        "def total_credits(rows):\n"
        "    return sum(r.credits for r in rows if not r.dropped)\n"
    ),
    "app/gpa.py": (
        "from app.credits import total_credits\n\n\n"
        "def gpa(rows):\n"
        "    return points(rows) / total_credits(rows)\n\n\n"
        "def points(rows):\n"
        "    return sum(r.points for r in rows)\n"
    ),
    "app/billing.py": (
        "from app import credits\n\n\n"
        "class Invoice:\n"
        "    def tuition(self, rows):\n"
        "        return 2500 * credits.total_credits(rows)\n\n\n"
        "class LateInvoice(Invoice):\n"
        "    def fee(self):\n"
        "        return 10\n"
    ),
    "tests/test_gpa.py": (
        "from app.gpa import gpa\n\n\n"
        "def test_gpa():\n"
        "    assert gpa([]) == 0\n"
    ),
}


@pytest.fixture
def graph():
    return CodeGraph.build(SOURCES, test_ids=["tests/test_gpa.py::test_gpa"])


def test_module_resolution():
    files = set(SOURCES)
    assert module_to_path("app.gpa", files) == "app/gpa.py"
    assert module_to_path("app", files) == "app/__init__.py"
    assert module_to_path(".credits", files, "app/gpa.py") == "app/credits.py"
    assert module_to_path("os", files) is None


def test_graph_calls_imports_and_inheritance(graph):
    target = "sym:app/credits.py::total_credits"
    callers = graph.callers(target, depth=3)
    assert set(callers) == {"sym:app/gpa.py::gpa", "sym:app/billing.py::Invoice.tuition", "sym:tests/test_gpa.py::test_gpa"}
    assert callers["sym:tests/test_gpa.py::test_gpa"] == 2
    assert graph.static_tests_for(target) == {"sym:tests/test_gpa.py::test_gpa": 2}
    assert graph.edges_of_type("sym:app/billing.py::LateInvoice", "inherits")[0][0] == "sym:app/billing.py::Invoice"
    assert graph.call_path("sym:tests/test_gpa.py::test_gpa", target) == [
        "sym:tests/test_gpa.py::test_gpa", "sym:app/gpa.py::gpa", target,
    ]
    assert graph.test_ids_for("sym:tests/test_gpa.py::test_gpa") == ["tests/test_gpa.py::test_gpa"]
    assert CodeGraph.evidence("calls", "sym:app/gpa.py::gpa", target) == "graph:calls:gpa->total_credits"


def test_graph_roundtrip(graph, tmp_path):
    graph.save(tmp_path / "g.json")
    loaded = CodeGraph.load(tmp_path / "g.json")
    assert loaded.stats() == graph.stats()


def test_tokenize_splits_identifiers():
    tokens = tokenize("calculateStudentCredits(rows) + self.compute_gpa")
    assert {"calculatestudentcredits", "calculate", "student", "credit", "compute", "gpa", "rows"} <= set(tokens)
    assert "self" not in tokens


def test_rrf_rewards_agreement():
    fused = reciprocal_rank_fusion({"a": ["x", "y", "z"], "b": ["y", "x"], "c": ["y"]})
    assert [i for i, _, _ in fused] == ["y", "x", "z"]
    assert fused[0][2] == ["a", "b", "c"]


def test_bm25_and_hashing_embedder():
    bm25 = BM25Index(["1", "2"], ["late fee grace period", "grade point average"])
    assert bm25.search("grace period")[0][0] == "1"
    emb = HashingEmbedder(256)
    vectors = emb.embed(["late fee grace", "late fee grace", "unrelated words here"])
    assert np.allclose(vectors[0], vectors[1])
    assert vectors[0] @ vectors[1] > vectors[0] @ vectors[2]
    store = NumpyStore()
    store.add(["a", "b"], vectors[1:])
    assert store.search(vectors[0], 1)[0][0] == "a"


def test_tfidf_reranker_prefers_relevant_text():
    scores = TfidfReranker().score("student credits", ["credits for a student", "fee schedule"])
    assert scores[0] > scores[1]


def test_hybrid_retriever_modes(graph, tmp_path):
    cfg = Config.load(tmp_path, environ={}, overrides={"project": {"root": "."}})
    chunks = code_chunks(SOURCES)
    emb = HashingEmbedder(256)
    store = NumpyStore()
    store.add([c.id for c in chunks], emb.embed([c.text for c in chunks]))
    index = RetrievalIndex(cfg, SOURCES, chunks, [], graph, DenseIndex(emb, store), DenseIndex(emb, NumpyStore()))
    index.code_search = LocalCodeSearch(tmp_path, SOURCES)
    retriever = HybridRetriever(index)
    seed = ["sym:app/credits.py::total_credits"]

    graph_only = retriever.search_code("total credits", seeds=seed, mode="graph", top_k=5)
    assert {r.symbol for r in graph_only} >= {"total_credits", "gpa", "Invoice.tuition"}
    assert all(r.sources == ["graph"] for r in graph_only)

    hybrid = retriever.search_code("total credits", seeds=seed, mode="hybrid", top_k=3)
    assert hybrid[0].symbol in {"total_credits", "gpa", "Invoice.tuition"}
    assert "total_credits" in retriever.queries

    lexical = retriever.search_code("tuition", mode="lexical", top_k=2)
    assert lexical[0].symbol == "Invoice.tuition"


def test_absorb_keeps_evidence_edges_across_line_shifts(graph):
    graph.g.add_node("commit:abc", kind="commit")
    graph.g.add_edge("sym:app/credits.py::total_credits", "commit:abc", type="modified_in")
    graph.g.add_edge("sym:tests/test_gpa.py::test_gpa", "sym:app/credits.py::total_credits", type="tests")
    shifted = dict(SOURCES)
    shifted["app/credits.py"] = "# new header\n\n" + SOURCES["app/credits.py"]
    head = CodeGraph.build(shifted)
    head.absorb(graph)
    target = "sym:app/credits.py::total_credits"
    assert head.node(target)["start"] == 3  # structure from the new code
    assert head.commits_for(target) == ["commit:abc"]
    assert head.covering_tests(target) == ["sym:tests/test_gpa.py::test_gpa"]
    assert head.test_ids_for("sym:tests/test_gpa.py::test_gpa") == ["tests/test_gpa.py::test_gpa"]
