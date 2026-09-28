"""Targeted mutation testing on the lines a pull request changed.

Instead of mutating the whole code base (which is what makes general-purpose mutation tools
slow) we generate mutants only on changed lines, and run only the tests that the head-revision
coverage says execute each mutated line. A surviving mutant is concrete evidence that the
tests would not notice a bug on that line — even though they pass today.

Operators (the classic mutmut / PIT set):
    cmp      <  <=  >  >=  ==  !=  is  is not  in  not in   -> their boundary / negated twin
    arith    +  -  *  /  //  %                             -> a sibling operator
    bool     and <-> or
    not      drop a unary ``not``
    const    integer n -> n + 1, True <-> False
    return   ``return expr`` -> ``return None``
    cond     ``if c:`` / ``while c:`` / ``elif c:`` -> ``not (c)``
"""

from __future__ import annotations

import ast
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sentinel.models import Mutant, MutationReport
from sentinel.verify.runner import run_tests

CMP_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
ARITH_SWAP = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div, ast.Mod: ast.FloorDiv,
}
BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}


@dataclass
class _Candidate:
    line: int
    operator: str
    start: tuple[int, int]  # (line, byte col)
    end: tuple[int, int]
    replacement: str
    original: str


def _segment(lines: list[bytes], start: tuple[int, int], end: tuple[int, int]) -> str:
    (l1, c1), (l2, c2) = start, end
    if l1 == l2:
        return lines[l1 - 1][c1:c2].decode("utf-8")
    parts = [lines[l1 - 1][c1:]] + lines[l1 : l2 - 1] + [lines[l2 - 1][:c2]]
    return b"\n".join(parts).decode("utf-8")


def _pos(node) -> tuple[tuple[int, int], tuple[int, int]]:
    return (node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset)


def candidates_for(source: str, lines_of_interest: set[int]) -> list[_Candidate]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    raw = source.encode("utf-8").split(b"\n")
    found: list[_Candidate] = []

    def add(node, operator: str, mutated_expr: str) -> None:
        start, end = _pos(node)
        found.append(_Candidate(node.lineno, operator, start, end, mutated_expr, _segment(raw, start, end)))

    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line is None or line not in lines_of_interest:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                swap = CMP_SWAP.get(type(op))
                if swap:
                    clone = ast.Compare(left=node.left, ops=[swap() if j == i else o for j, o in enumerate(node.ops)], comparators=node.comparators)
                    add(node, "cmp", f"({ast.unparse(clone)})")
        elif isinstance(node, ast.BinOp):
            swap = ARITH_SWAP.get(type(node.op))
            if swap:
                add(node, "arith", f"({ast.unparse(ast.BinOp(left=node.left, op=swap(), right=node.right))})")
        elif isinstance(node, ast.BoolOp):
            swap = BOOL_SWAP.get(type(node.op))
            if swap:
                add(node, "bool", f"({ast.unparse(ast.BoolOp(op=swap(), values=node.values))})")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            add(node, "not", f"({ast.unparse(node.operand)})")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                add(node, "const", str(not node.value))
            elif isinstance(node.value, int):
                add(node, "const", str(node.value + 1))
        elif isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                start, end = _pos(node.value)
                found.append(_Candidate(node.lineno, "return", start, end, "None", _segment(raw, start, end)))
        elif isinstance(node, (ast.If, ast.While)):
            start, end = _pos(node.test)
            text = _segment(raw, start, end)
            found.append(_Candidate(node.lineno, "cond", start, end, f"not ({text})", text))
    return found


def apply(source: str, candidate: _Candidate) -> str | None:
    raw = source.encode("utf-8").split(b"\n")
    (l1, c1), (l2, c2) = candidate.start, candidate.end
    replacement = candidate.replacement.encode("utf-8")
    head = raw[l1 - 1][:c1]
    tail = raw[l2 - 1][c2:]
    mutated_lines = raw[: l1 - 1] + [head + replacement + tail] + raw[l2:]
    mutated = b"\n".join(mutated_lines).decode("utf-8")
    try:
        compile(mutated, "<mutant>", "exec")
    except SyntaxError:
        return None
    return mutated if mutated != source else None


def generate_mutants(sources: dict[str, str], changed: dict[str, set[int]], symbols: dict[tuple[str, int], str] | None = None, limit: int = 24) -> list[tuple[Mutant, str]]:
    """Mutants on changed lines, spread across lines and operators, at most ``limit``."""
    per_line: dict[tuple[str, int], list[tuple[Mutant, str]]] = defaultdict(list)
    for path, lines in sorted(changed.items()):
        source = sources.get(path)
        if not source or not lines:
            continue
        for index, cand in enumerate(candidates_for(source, lines)):
            mutated = apply(source, cand)
            if mutated is None:
                continue
            mutant = Mutant(
                id=f"m:{path}:{cand.line}:{cand.operator}:{index}",
                file=path,
                line=cand.line,
                operator=cand.operator,
                original=cand.original.strip()[:120],
                mutated=cand.replacement.strip()[:120],
                symbol=(symbols or {}).get((path, cand.line)),
            )
            per_line[(path, cand.line)].append((mutant, mutated))

    # Round-robin over lines so a single complex line cannot eat the whole budget.
    selected: list[tuple[Mutant, str]] = []
    queues = [sorted(v, key=lambda mv: (mv[0].operator, mv[0].id)) for _, v in sorted(per_line.items())]
    while queues and len(selected) < limit:
        for queue in list(queues):
            if len(selected) >= limit:
                break
            selected.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
    return selected


def run_mutation(
    workspace: Path,
    mutants: list[tuple[Mutant, str]],
    line_tests: dict[tuple[str, int], set[str]],
    *,
    broken_tests: set[str] | None = None,
    per_mutant_timeout_s: float = 60,
    scratch: Path | None = None,
    import_lines: set[tuple[str, int]] | None = None,
    fallback_tests: list[str] | None = None,
) -> MutationReport:
    """Execute each mutant against the tests that cover its line, restoring the file after.

    Lines that only run at import time (module constants, say) have no per-test coverage;
    their mutants are run against ``fallback_tests`` (the PR's selected tests) instead.
    """
    started = time.time()
    broken_tests = broken_tests or set()
    for mutant, mutated_source in mutants:
        covering = line_tests.get((mutant.file, mutant.line), set())
        if not covering and (mutant.file, mutant.line) in (import_lines or set()):
            covering = set(fallback_tests or [])
        tests = sorted(t for t in covering if t not in broken_tests)
        if not tests:
            mutant.status = "survived"  # no test executes this line: nothing could notice the bug
            continue
        target = workspace / mutant.file
        original = target.read_text(encoding="utf-8")
        try:
            target.write_text(mutated_source, encoding="utf-8", newline="\n")
            result = run_tests(workspace, tests, stop_first=True, timeout_s=per_mutant_timeout_s, scratch=scratch)
        finally:
            target.write_text(original, encoding="utf-8", newline="\n")
        if result.timed_out:
            mutant.status = "timeout"
        elif result.run.failed:
            mutant.status = "killed"
            mutant.killed_by = [o.nodeid for o in result.run.failed][:5]
        elif result.run.collection_error or (result.exit_code not in (0,) and not result.run.outcomes):
            mutant.status = "killed"  # the mutant broke import/collection: tests noticed
            mutant.killed_by = ["<collection>"]
        else:
            mutant.status = "survived"
    return MutationReport(mutants=[m for m, _ in mutants], duration_s=round(time.time() - started, 2))
