"""C3: API / ripple changes — a signature changes and only some callers follow."""

from __future__ import annotations

import ast
import re

from bench.generate.base import PRSpec, Snapshot, compiles, interesting_sources, rng_for

NEW_PARAMS = ["strict", "include_pending", "round_result", "validate"]


def _params_close(source: str, fn: ast.FunctionDef) -> tuple[int, int] | None:
    """(line, byte col) of the ')' that closes ``fn``'s parameter list."""
    lines = source.encode("utf-8").split(b"\n")
    body = fn.body[0]
    depth, started = 0, False
    for line_no in range(fn.lineno, body.lineno + 1):
        raw = lines[line_no - 1]
        start_col = fn.col_offset if line_no == fn.lineno else 0
        for col in range(start_col, len(raw)):
            ch = raw[col : col + 1]
            if ch == b"(":
                depth += 1
                started = True
            elif ch == b")" and started:
                depth -= 1
                if depth == 0:
                    return line_no, col
    return None


def _insert(source: str, line: int, col: int, text: str) -> str:
    lines = source.encode("utf-8").split(b"\n")
    lines[line - 1] = lines[line - 1][:col] + text.encode("utf-8") + lines[line - 1][col:]
    return b"\n".join(lines).decode("utf-8")


def _calls_to(source: str, name: str) -> list[ast.Call]:
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if target == name:
                calls.append(node)
    return sorted(calls, key=lambda c: (c.lineno, c.col_offset))


def add_parameter(snapshot: Snapshot, rng, required: bool) -> PRSpec | None:
    sources = interesting_sources(snapshot)
    candidates = []
    for path in sources:
        tree = ast.parse(snapshot.read(path))
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
                continue
            callers = [p for p in sources if p != path and re.search(rf"\b{re.escape(fn.name)}\(", snapshot.read(p) or "")]
            if len(callers) >= (2 if required else 1):
                candidates.append((path, fn, callers))
    if not candidates:
        return None
    path, fn, callers = rng.choice(candidates)
    source = snapshot.read(path)
    close = _params_close(source, fn)
    if close is None:
        return None
    param = rng.choice(NEW_PARAMS)
    has_star = fn.args.kwonlyargs or fn.args.vararg
    has_args = bool(fn.args.args or fn.args.kwonlyargs or fn.args.posonlyargs)
    decl = f"{param}: bool" if required else f"{param}: bool = False"
    text = (", " if has_args else "") + ("" if has_star else "*, ") + decl
    updated = {path: _insert(source, *close, text)}
    if not compiles(updated[path]):
        return None

    if required:
        # Update only the first caller; the rest keep the old call shape.
        caller = sorted(callers)[0]
        caller_source = snapshot.read(caller)
        calls = _calls_to(caller_source, fn.name)
        if not calls:
            return None
        call = calls[0]
        separator = ", " if (call.args or call.keywords) else ""
        updated[caller] = _insert(caller_source, call.end_lineno, call.end_col_offset - 1, f"{separator}{param}=False")
        if not compiles(updated[caller]):
            return None
        title = f"Require `{param}` in {fn.name}"
        body = f"`{fn.name}` now needs an explicit `{param}` flag. Updated the call site in {caller.rsplit('/', 1)[-1]}."
    else:
        title = f"Add optional `{param}` flag to {fn.name}"
        body = "Backwards compatible: defaults preserve current behaviour."
    return PRSpec("", "", "", "C3", title, body, updated, "add_required_param" if required else "add_optional_param",
                  {"path": path, "function": fn.name, "callers": callers})


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    rng = rng_for(seed, "C3")
    specs, attempts = [], 0
    while len(specs) < count and attempts < count * 10:
        attempts += 1
        spec = add_parameter(snapshot, rng, required=attempts % 3 != 0)
        if spec and all(spec.edits != s.edits for s in specs):
            specs.append(spec)
    return specs
