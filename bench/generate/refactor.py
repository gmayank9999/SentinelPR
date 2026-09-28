"""C1: behaviour-preserving refactors."""

from __future__ import annotations

import ast
import re

from bench.generate.base import PRSpec, Snapshot, compiles, human_name, interesting_sources, rng_for

RENAMES = {"total": "running_total", "result": "outcome", "records": "rows", "history": "past", "report": "entries",
           "current": "active", "course": "offering", "credits": "credit_count", "items": "line_items", "payments": "paid",
           "student": "learner", "enrollment": "entry", "values": "series", "rate": "fee_rate", "load": "current_load",
           "tuition": "base_tuition", "done": "completed", "missing": "absent", "latest": "newest", "points": "quality"}

DOC_TEMPLATES = [
    "{Name}.",
    "Return the {name} for the given inputs.",
    "Compute {name}. Behaviour is covered by the unit tests.",
]


def _local_names(fn: ast.FunctionDef) -> set[str]:
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
    if fn.args.vararg:
        params.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        params.add(fn.args.kwarg.arg)
    assigned, keywords = set(), set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            keywords.add(node.arg)  # renaming these would also rename keyword arguments
    return assigned - params - keywords


def rename_local(snapshot: Snapshot, path: str, rng) -> PRSpec | None:
    source = snapshot.read(path)
    tree = ast.parse(source)
    candidates = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for name in sorted(_local_names(fn)):
            if name in RENAMES:
                candidates.append((fn, name))
    if not candidates:
        return None
    fn, name = rng.choice(candidates)
    new_name = RENAMES[name]
    lines = source.split("\n")
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}\b")
    for i in range(fn.lineno - 1, fn.end_lineno):
        # leave string literals alone; rename identifiers only
        parts = re.split(r"(\"[^\"]*\"|'[^']*')", lines[i])
        lines[i] = "".join(p if p.startswith(("'", '"')) else pattern.sub(new_name, p) for p in parts)
    updated = "\n".join(lines)
    if updated == source or not compiles(updated):
        return None
    return PRSpec("", "", "", "C1", f"Rename `{name}` to `{new_name}` in {fn.name}", f"Readability: `{new_name}` says what the variable holds. No behaviour change.",
                  {path: updated}, "rename_local", {"function": fn.name, "path": path})


def add_docstring(snapshot: Snapshot, path: str, rng) -> PRSpec | None:
    source = snapshot.read(path)
    tree = ast.parse(source)
    undocumented = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and ast.get_docstring(n) is None and n.body]
    if not undocumented:
        return None
    fn = rng.choice(undocumented)
    first = fn.body[0]
    indent = " " * first.col_offset
    name = human_name(fn.name)
    doc = rng.choice(DOC_TEMPLATES).format(name=name, Name=name.capitalize())
    lines = source.split("\n")
    lines.insert(first.lineno - 1, f'{indent}"""{doc}"""')
    updated = "\n".join(lines)
    if not compiles(updated):
        return None
    return PRSpec("", "", "", "C1", f"Document {fn.name}", "Adds a docstring.", {path: updated}, "add_docstring", {"function": fn.name, "path": path})


def sort_imports(snapshot: Snapshot, path: str, rng) -> PRSpec | None:
    source = snapshot.read(path)
    lines = source.split("\n")
    block = [i for i, l in enumerate(lines) if l.startswith(("from unierp", "import unierp", f"from {snapshot.package}"))]
    if len(block) < 2 or block != list(range(block[0], block[-1] + 1)):
        return None
    current = [lines[i] for i in block]
    shuffled = sorted(current, key=lambda l: l.split(" import ")[0], reverse=True)
    if shuffled == current:
        return None
    lines[block[0] : block[-1] + 1] = shuffled
    return PRSpec("", "", "", "C1", f"Reorder imports in {path.rsplit('/', 1)[-1]}", "Import tidy-up.", {path: "\n".join(lines)}, "reorder_imports", {"path": path})


def extract_constant(snapshot: Snapshot, path: str, rng) -> PRSpec | None:
    """Pull a literal number out into a module constant, used in exactly the same place."""
    source = snapshot.read(path)
    tree = ast.parse(source)
    targets = []
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool) and node.value not in (0, 1) \
                    and node.lineno == node.end_lineno:
                targets.append((fn, node))
    if not targets:
        return None
    fn, node = rng.choice(targets)
    const = f"_{fn.name.upper()}_LIMIT"
    lines = source.split("\n")
    raw = lines[node.lineno - 1].encode("utf-8")
    lines[node.lineno - 1] = (raw[: node.col_offset] + const.encode() + raw[node.end_col_offset :]).decode("utf-8")
    insert_at = fn.lineno - 1 - len(fn.decorator_list)
    lines[insert_at:insert_at] = [f"{const} = {node.value!r}", "", ""]
    updated = "\n".join(lines)
    if not compiles(updated):
        return None
    return PRSpec("", "", "", "C1", f"Name the magic number in {fn.name}", f"Replaces a literal with `{const}`.", {path: updated}, "extract_constant", {"function": fn.name, "path": path})


GENERATORS = (rename_local, add_docstring, sort_imports, extract_constant)


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    rng = rng_for(seed, "C1")
    sources = interesting_sources(snapshot)
    specs, attempts = [], 0
    while len(specs) < count and attempts < count * 20:
        attempts += 1
        generator = GENERATORS[attempts % len(GENERATORS)]
        spec = generator(snapshot, rng.choice(sources), rng)
        if spec and all(spec.edits != s.edits for s in specs):
            specs.append(spec)
    return specs
