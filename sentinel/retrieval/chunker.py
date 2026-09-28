"""Structural parsing and AST chunking of Python source.

tree-sitter is the primary parser: it is fast, tolerant of syntax errors (useful for a
half-finished PR) and gives exact byte ranges. When the grammar is not installed we fall
back to the standard library ``ast`` module, which produces the same ``ModuleInfo``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache

MAX_CHUNK_LINES = 120


@dataclass
class Symbol:
    qualname: str
    name: str
    kind: str  # function | method | class
    start: int  # first line, including decorators (1-based, inclusive)
    end: int
    def_line: int  # line holding the def/class keyword
    signature: str
    docstring: str | None = None
    parent: str | None = None
    calls: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)

    def contains(self, line: int) -> bool:
        return self.start <= line <= self.end


@dataclass
class ImportRef:
    module: str  # absolute or relative module ("unierp.exam.grades", ".grades")
    name: str | None  # imported name for "from x import name"
    alias: str | None = None

    @property
    def bound_name(self) -> str:
        if self.alias:
            return self.alias
        if self.name:
            return self.name
        return self.module.split(".")[0]


@dataclass
class ModuleInfo:
    path: str
    symbols: list[Symbol]
    imports: list[ImportRef]
    docstring: str | None
    line_count: int
    parser: str
    module_calls: list[str] = field(default_factory=list)
    syntax_error: bool = False

    def symbol(self, qualname: str) -> Symbol | None:
        for s in self.symbols:
            if s.qualname == qualname:
                return s
        return None

    def innermost(self, line: int) -> Symbol | None:
        best = None
        for s in self.symbols:
            if s.contains(line) and (best is None or s.end - s.start <= best.end - best.start):
                best = s
        return best


@dataclass
class Chunk:
    id: str
    path: str
    symbol: str | None
    kind: str
    start: int
    end: int
    text: str

    @property
    def evidence_id(self) -> str:
        return self.id


# ---------------------------------------------------------------------------
# tree-sitter backend
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _ts_parser():
    try:
        import tree_sitter_python
        from tree_sitter import Language, Parser
    except ImportError:
        return None
    return Parser(Language(tree_sitter_python.language()))


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _call_name(node, source: bytes) -> str | None:
    target = node.child_by_field_name("function")
    if target is None:
        return None
    if target.type == "identifier":
        return _text(target, source)
    if target.type == "attribute":
        attr = target.child_by_field_name("attribute")
        return _text(attr, source) if attr is not None else None
    return None


def _collect_calls(node, source: bytes, out: list[str], stop_types: tuple[str, ...]) -> None:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in stop_types:
            continue
        if current.type == "call":
            name = _call_name(current, source)
            if name:
                out.append(name)
        stack.extend(current.children)


def _ts_docstring(body, source: bytes) -> str | None:
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
        try:
            return ast.literal_eval(_text(first.named_children[0], source))
        except (ValueError, SyntaxError):
            return None
    return None


def _parse_tree_sitter(source_text: str, path: str) -> ModuleInfo | None:
    parser = _ts_parser()
    if parser is None:
        return None
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node
    symbols: list[Symbol] = []
    imports: list[ImportRef] = []
    module_calls: list[str] = []
    definition_types = ("function_definition", "class_definition", "decorated_definition")

    def visit(node, parent: Symbol | None) -> None:
        for child in node.children:
            outer = child
            decorators: list[str] = []
            definition = child
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition")
                decorators = [_text(d, source).lstrip("@").strip() for d in child.children if d.type == "decorator"]
                if definition is None:
                    continue
            if definition.type not in ("function_definition", "class_definition"):
                if parent is None:
                    if child.type in ("import_statement", "import_from_statement"):
                        imports.extend(_ts_imports(child, source))
                    else:
                        _collect_calls(child, source, module_calls, definition_types)
                continue

            name_node = definition.child_by_field_name("name")
            body = definition.child_by_field_name("body")
            name = _text(name_node, source)
            is_class = definition.type == "class_definition"
            kind = "class" if is_class else ("method" if parent is not None and parent.kind == "class" else "function")
            qualname = f"{parent.qualname}.{name}" if parent else name
            header_end = body.start_byte if body is not None else definition.end_byte
            signature = " ".join(source[definition.start_byte : header_end].decode("utf-8", "replace").split()).rstrip(":").rstrip()
            symbol = Symbol(
                qualname=qualname,
                name=name,
                kind=kind,
                start=outer.start_point[0] + 1,
                end=outer.end_point[0] + 1,
                def_line=definition.start_point[0] + 1,
                signature=signature,
                docstring=_ts_docstring(body, source),
                parent=parent.qualname if parent else None,
                decorators=decorators,
            )
            if is_class:
                superclasses = definition.child_by_field_name("superclasses")
                if superclasses is not None:
                    symbol.bases = [_text(a, source) for a in superclasses.named_children if a.type in ("identifier", "attribute")]
            if body is not None:
                _collect_calls(body, source, symbol.calls, definition_types)
            symbols.append(symbol)
            if body is not None:
                visit(body, symbol)

    visit(root, None)
    return ModuleInfo(
        path=path,
        symbols=symbols,
        imports=imports,
        docstring=_ts_docstring(root, source),
        line_count=source_text.count("\n") + (0 if source_text.endswith("\n") else 1),
        parser="tree-sitter",
        module_calls=module_calls,
        syntax_error=root.has_error,
    )


def _ts_imports(node, source: bytes) -> list[ImportRef]:
    refs: list[ImportRef] = []
    if node.type == "import_statement":
        for child in node.named_children:
            if child.type == "dotted_name":
                refs.append(ImportRef(_text(child, source), None))
            elif child.type == "aliased_import":
                refs.append(ImportRef(_text(child.child_by_field_name("name"), source), None, _text(child.child_by_field_name("alias"), source)))
        return refs
    module_node = node.child_by_field_name("module_name")
    module = _text(module_node, source) if module_node is not None else ""
    for child in node.named_children:
        # tree-sitter hands out fresh wrapper objects, so compare positions, not identity.
        if module_node is not None and child.start_byte == module_node.start_byte:
            continue
        if child.type == "dotted_name":
            refs.append(ImportRef(module, _text(child, source)))
        elif child.type == "aliased_import":
            refs.append(ImportRef(module, _text(child.child_by_field_name("name"), source), _text(child.child_by_field_name("alias"), source)))
        elif child.type == "wildcard_import":
            refs.append(ImportRef(module, "*"))
    return refs


# ---------------------------------------------------------------------------
# ast fallback
# ---------------------------------------------------------------------------


class _AstVisitor(ast.NodeVisitor):
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.symbols: list[Symbol] = []
        self.stack: list[Symbol] = []

    def _signature(self, node) -> str:
        first = node.body[0]
        start_line, start_col = node.lineno - 1, node.col_offset
        end_line, end_col = first.lineno - 1, first.col_offset
        if start_line == end_line:
            text = self.lines[start_line][start_col:end_col]
        else:
            text = "\n".join([self.lines[start_line][start_col:]] + self.lines[start_line + 1 : end_line] + [self.lines[end_line][:end_col]])
        return " ".join(text.split()).rstrip(":").rstrip()

    def _define(self, node, kind: str) -> None:
        parent = self.stack[-1] if self.stack else None
        if kind == "function" and parent is not None and parent.kind == "class":
            kind = "method"
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        symbol = Symbol(
            qualname=f"{parent.qualname}.{node.name}" if parent else node.name,
            name=node.name,
            kind=kind,
            start=start,
            end=node.end_lineno or node.lineno,
            def_line=node.lineno,
            signature=self._signature(node),
            docstring=ast.get_docstring(node),
            parent=parent.qualname if parent else None,
            decorators=[ast.unparse(d) for d in node.decorator_list],
        )
        if isinstance(node, ast.ClassDef):
            symbol.bases = [ast.unparse(b) for b in node.bases]
        symbol.calls = _calls_outside_nested(node.body)
        self.symbols.append(symbol)
        self.stack.append(symbol)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):  # noqa: N802
        self._define(node, "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):  # noqa: N802
        self._define(node, "class")


def _calls_outside_nested(body: list[ast.stmt]) -> list[str]:
    """Calls in ``body``; calls inside nested definitions belong to those definitions."""
    calls: list[str] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            name = _ast_call_name(node)
            if name:
                calls.append(name)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _ast_call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _parse_ast(source: str, path: str) -> ModuleInfo:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ModuleInfo(path, [], [], None, source.count("\n") + 1, "ast", syntax_error=True)
    visitor = _AstVisitor(source.splitlines())
    visitor.visit(tree)
    imports: list[ImportRef] = []
    module_calls: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(ImportRef(a.name, None, a.asname) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.extend(ImportRef(module, a.name, a.asname) for a in node.names)
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_calls.extend(n for n in (_ast_call_name(c) for c in ast.walk(node) if isinstance(c, ast.Call)) if n)
    return ModuleInfo(path, visitor.symbols, imports, ast.get_docstring(tree), source.count("\n") + 1, "ast", module_calls)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def parse_python(source: str, path: str = "", backend: str = "auto") -> ModuleInfo:
    if backend in ("auto", "tree-sitter"):
        info = _parse_tree_sitter(source, path)
        if info is not None:
            return info
        if backend == "tree-sitter":
            raise RuntimeError("tree-sitter-python is not installed")
    return _parse_ast(source, path)


def chunk_module(source: str, path: str, info: ModuleInfo | None = None, max_lines: int = MAX_CHUNK_LINES) -> list[Chunk]:
    """One chunk per function/method, one per class header, one for module-level code.

    Methods are chunked individually, so a class chunk only holds the lines of the class that
    are not inside a method (header, docstring, attributes). Very long functions are split at
    statement boundaries (blank lines) into windows of at most ``max_lines`` lines.
    """
    info = info or parse_python(source, path)
    lines = source.splitlines()
    chunks: list[Chunk] = []
    covered = [False] * (len(lines) + 2)

    functions = [s for s in info.symbols if s.kind in ("function", "method")]
    top_functions = [f for f in functions if not any(o is not f and o.kind in ("function", "method") and o.start <= f.start and f.end <= o.end for o in functions)]
    for sym in top_functions:
        for start, end in _windows(lines, sym.start, sym.end, max_lines):
            chunks.append(_make_chunk(path, sym.qualname, sym.kind, start, end, lines, sym))
        for n in range(sym.start, sym.end + 1):
            covered[n] = True

    for cls in (s for s in info.symbols if s.kind == "class"):
        own = [n for n in range(cls.start, cls.end + 1) if not covered[n]]
        if own:
            chunks.append(_make_chunk(path, cls.qualname, "class", own[0], own[-1], lines, cls, only=set(own)))
            for n in own:
                covered[n] = True

    module_lines = [n for n in range(1, len(lines) + 1) if not covered[n] and lines[n - 1].strip()]
    if module_lines:
        for start, end in _windows(lines, module_lines[0], module_lines[-1], max_lines):
            chunks.append(_make_chunk(path, None, "module", start, end, lines, None, only=set(module_lines)))
    return sorted(chunks, key=lambda c: c.start)


def _windows(lines: list[str], start: int, end: int, max_lines: int) -> list[tuple[int, int]]:
    if end - start + 1 <= max_lines:
        return [(start, end)]
    windows = []
    cursor = start
    while cursor <= end:
        limit = min(cursor + max_lines - 1, end)
        split = limit
        if limit < end:
            for n in range(limit, cursor + max_lines // 2, -1):
                if not lines[n - 1].strip():
                    split = n
                    break
        windows.append((cursor, split))
        cursor = split + 1
    return windows


def _make_chunk(path, qualname, kind, start, end, lines, symbol, only: set[int] | None = None) -> Chunk:
    body = [lines[n - 1] for n in range(start, end + 1) if only is None or n in only]
    header = f"# file: {path}"
    if qualname:
        header += f" | {kind} {qualname}"
    if symbol is not None and symbol.docstring and kind != "module":
        header += f"\n# doc: {symbol.docstring.strip().splitlines()[0]}"
    text = header + "\n" + "\n".join(body)
    return Chunk(id=f"chunk:{path}#L{start}-{end}", path=path, symbol=qualname, kind=kind, start=start, end=end, text=text)


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def identifiers(text: str) -> list[str]:
    return _IDENT.findall(text)


def split_identifier(name: str) -> list[str]:
    """``calculateStudentCredits`` / ``compute_gpa`` -> sub-words, lowercased."""
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
    return [p.lower() for p in parts if p]
