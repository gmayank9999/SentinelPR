"""Detect edits that cannot change behaviour: renamed locals, docstrings, formatting.

Two versions of a function are *alpha-equivalent* when their ASTs are identical once the
docstring is dropped and every local name (parameters and assigned variables) is replaced by a
positional placeholder. Mutating such a function would only measure how well the *old* code was
tested — nothing this PR introduced — so the verifier leaves it out of mutation.
"""

from __future__ import annotations

import ast
import copy


def _find(tree: ast.AST, qualname: str) -> ast.AST | None:
    node: ast.AST = tree
    for part in qualname.split("."):
        body = getattr(node, "body", [])
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if node is None:
            return None
    return node


def canonical(source: str | None, qualname: str) -> str | None:
    if not source:
        return None
    try:
        fn = _find(ast.parse(source), qualname)
    except SyntaxError:
        return None
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    fn = copy.deepcopy(fn)
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) \
            and isinstance(fn.body[0].value.value, str):
        fn.body = fn.body[1:] or [ast.Pass()]
    args = fn.args
    local = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    local |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    local |= {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    mapping: dict[str, str] = {}

    class Rename(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.Name:
            if node.id in local:
                node.id = mapping.setdefault(node.id, f"_v{len(mapping)}")
            return node

        def visit_arg(self, node: ast.arg) -> ast.arg:
            if node.arg in local:
                node.arg = mapping.setdefault(node.arg, f"_v{len(mapping)}")
            node.annotation = self.visit(node.annotation) if node.annotation else None
            return node

    fn.name = "_f"
    return ast.dump(Rename().visit(fn), annotate_fields=False, include_attributes=False)


def behaviour_preserving(qualname: str, old_source: str | None, new_source: str | None) -> bool:
    before, after = canonical(old_source, qualname), canonical(new_source, qualname)
    return before is not None and before == after
