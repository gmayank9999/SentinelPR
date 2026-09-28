"""C2 and hidden faults: single mutations of real functions, dressed as plausible PRs.

Whether a fault becomes C2 ("the visible suite catches it") or HF ("all visible tests pass,
only the hidden oracle catches it") is decided later by execution.
"""

from __future__ import annotations

from bench.generate.base import PRSpec, Snapshot, interesting_sources, rng_for
from sentinel.verify.mutation import apply, candidates_for

TITLES = {
    "cmp": ["Adjust boundary check in {fn}", "Tighten comparison in {fn}", "Clarify threshold handling in {fn}"],
    "arith": ["Simplify arithmetic in {fn}", "Refine calculation in {fn}"],
    "bool": ["Simplify condition in {fn}", "Clean up guard in {fn}"],
    "not": ["Simplify negation in {fn}"],
    "const": ["Tune constant in {fn}", "Update default in {fn}"],
    "return": ["Early return cleanup in {fn}"],
    "cond": ["Restructure branch in {fn}", "Invert condition in {fn}"],
}
BODIES = [
    "Small cleanup while reading this code.",
    "Makes the logic easier to follow.",
    "Follow-up to review comments on the previous PR.",
    "Minor tweak; existing tests pass locally.",
]


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    rng = rng_for(seed, "faults")
    specs: list[PRSpec] = []
    pool = []
    for path in interesting_sources(snapshot):
        source = snapshot.read(path)
        for fn in snapshot.functions(path):
            body_lines = set(range(fn.def_line + 1, fn.end + 1))
            for cand in candidates_for(source, body_lines):
                pool.append((path, fn, cand))
    rng.shuffle(pool)
    seen = set()
    for path, fn, cand in pool:
        if len(specs) >= count:
            break
        key = (path, cand.line, cand.operator)
        if key in seen:
            continue
        mutated = apply(snapshot.read(path), cand)
        if mutated is None:
            continue
        seen.add(key)
        title = rng.choice(TITLES[cand.operator]).format(fn=fn.name)
        specs.append(PRSpec("", "", "", "FAULT", title, rng.choice(BODIES), {path: mutated}, f"mutation:{cand.operator}",
                            {"path": path, "function": fn.qualname, "line": cand.line, "original": cand.original.strip()[:80],
                             "mutated": cand.replacement.strip()[:80]}))
    return specs
