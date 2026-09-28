import pytest

from sentinel.retrieval.chunker import chunk_module, parse_python, split_identifier

SOURCE = '''"""Module doc."""
import os
from pkg.mod import thing as alias, other


@decorator
def top(a, b: int = 1) -> int:
    """Top level."""
    return helper(a) + os.path.join("x")


class Store(Base):
    """A store."""

    size = 3

    def get(self, key):
        return self.lookup(key)

    def put(self, key, value):
        def inner():
            return nested_call()
        return top(key, value)


setup()
'''


@pytest.mark.parametrize("backend", ["tree-sitter", "ast"])
def test_symbols(backend):
    info = parse_python(SOURCE, "m.py", backend=backend)
    assert [s.qualname for s in info.symbols] == ["top", "Store", "Store.get", "Store.put", "Store.put.inner"]
    top = info.symbol("top")
    assert (top.start, top.def_line, top.end) == (6, 7, 9)
    assert top.signature == "def top(a, b: int = 1) -> int"
    assert top.docstring == "Top level."
    assert sorted(top.calls) == ["helper", "join"]
    assert info.symbol("Store").bases == ["Base"]
    assert info.symbol("Store.get").kind == "method"
    # calls inside nested functions are attributed to the nested function
    assert info.symbol("Store.put").calls == ["top"]
    assert info.symbol("Store.put.inner").calls == ["nested_call"]
    assert info.docstring == "Module doc."
    assert "setup" in info.module_calls


@pytest.mark.parametrize("backend", ["tree-sitter", "ast"])
def test_imports(backend):
    info = parse_python(SOURCE, "m.py", backend=backend)
    bound = {ref.bound_name: (ref.module, ref.name) for ref in info.imports}
    assert bound == {"os": ("os", None), "alias": ("pkg.mod", "thing"), "other": ("pkg.mod", "other")}


def test_innermost_symbol():
    info = parse_python(SOURCE, "m.py")
    assert info.innermost(18).qualname == "Store.get"
    assert info.innermost(15).qualname == "Store"
    assert info.innermost(2) is None


def test_chunks_cover_methods_classes_and_module():
    chunks = chunk_module(SOURCE, "m.py")
    by_symbol = {c.symbol: c for c in chunks}
    assert set(by_symbol) == {None, "top", "Store", "Store.get", "Store.put"}
    assert by_symbol["Store.get"].id == "chunk:m.py#L17-18"
    assert "size = 3" in by_symbol["Store"].text
    assert "def get" not in by_symbol["Store"].text
    assert by_symbol["top"].text.startswith("# file: m.py | function top\n# doc: Top level.")


def test_long_functions_are_split():
    body = "\n".join(f"    x{i} = {i}" + ("\n" if i % 10 == 0 else "") for i in range(300))
    chunks = chunk_module(f"def big():\n{body}\n", "big.py", max_lines=50)
    assert len(chunks) > 5
    assert all(c.end - c.start < 50 for c in chunks)
    assert chunks[0].start == 1


def test_syntax_errors_are_tolerated():
    info = parse_python("def ok():\n    return 1\n\ndef broken(:\n", "x.py")
    assert info.syntax_error
    assert info.symbol("ok") is not None


def test_split_identifier():
    assert split_identifier("calculateStudentCredits") == ["calculate", "student", "credits"]
    assert split_identifier("compute_gpa") == ["compute", "gpa"]


def test_parses_real_unierp_sources(unierp_sources):
    for path, source in unierp_sources.items():
        ts = parse_python(source, path, backend="tree-sitter")
        py = parse_python(source, path, backend="ast")
        assert [s.qualname for s in ts.symbols] == [s.qualname for s in py.symbols], path
        assert [(s.start, s.end) for s in ts.symbols] == [(s.start, s.end) for s in py.symbols], path
