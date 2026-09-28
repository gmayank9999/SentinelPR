"""C6: test-only and docs-only changes. They should never be blocked."""

from __future__ import annotations

import ast

from bench.generate.base import PRSpec, Snapshot, compiles, rng_for

DOC_NOTES = [
    "\n## Troubleshooting\n\nIf the service does not start, check that port 8000 is free.\n",
    "\n## Contributing\n\nRun the test suite before opening a pull request.\n",
    "\n## Glossary\n\n* **CGPA** — cumulative grade point average.\n",
]


def duplicate_test(snapshot: Snapshot, rng) -> PRSpec | None:
    path = rng.choice(snapshot.test_files)
    source = snapshot.read(path)
    tree = ast.parse(source)
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_") and not n.decorator_list and n.end_lineno - n.lineno < 12]
    if not tests:
        return None
    fn = rng.choice(tests)
    lines = source.split("\n")
    copy = lines[fn.lineno - 1 : fn.end_lineno]
    copy[0] = copy[0].replace(f"def {fn.name}(", f"def {fn.name}_again(", 1)
    updated = source.rstrip("\n") + "\n\n\n" + "\n".join(copy) + "\n"
    if not compiles(updated):
        return None
    return PRSpec("", "", "", "C6", f"Add regression test next to {fn.name}", "Test-only change.", {path: updated}, "add_test", {"path": path, "test": fn.name})


def add_comment(snapshot: Snapshot, rng) -> PRSpec | None:
    path = rng.choice([p for p in snapshot.source_files if "/api/" not in p])
    source = snapshot.read(path)
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not functions:
        return None
    fn = rng.choice(functions)
    lines = source.split("\n")
    lines.insert(fn.lineno - 1 - len(fn.decorator_list), f"# See the module docstring for the policy behind {fn.name}.")
    updated = "\n".join(lines)
    return PRSpec("", "", "", "C6", f"Comment {fn.name}", "Comment-only change.", {path: updated}, "add_comment", {"path": path})


def update_docs(snapshot: Snapshot, rng) -> PRSpec | None:
    readme = snapshot.read("README.md")
    if readme is None:
        return None
    note = rng.choice(DOC_NOTES)
    if note in readme:
        return None
    return PRSpec("", "", "", "C6", "Improve README", "Docs-only change.", {"README.md": readme.rstrip("\n") + "\n" + note}, "update_docs", {})


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    rng = rng_for(seed, "C6")
    generators = (duplicate_test, add_comment, update_docs, duplicate_test)
    specs, attempts = [], 0
    while len(specs) < count and attempts < count * 10:
        attempts += 1
        spec = generators[attempts % len(generators)](snapshot, rng)
        if spec and all(spec.edits != s.edits for s in specs):
            specs.append(spec)
    return specs
