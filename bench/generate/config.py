"""C5: configuration and dependency changes — module constants and requirement pins."""

from __future__ import annotations

import ast
import re

from bench.generate.base import PRSpec, Snapshot, compiles, interesting_sources, rng_for

DECIMAL_CALL = re.compile(r'Decimal\("(?P<num>-?\d+(?:\.\d+)?)"\)')


def _constants(source: str) -> list[tuple[ast.Assign, str]]:
    tree = ast.parse(source)
    found = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id.isupper() and node.value is not None and node.lineno == node.end_lineno:
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
                    found.append((node, target.id))
                elif isinstance(value, ast.Call) and getattr(value.func, "id", "") in ("Decimal", "timedelta"):
                    found.append((node, target.id))
    return found


def _nudge(number: str, rng) -> str:
    value = float(number)
    if "." in number:
        places = len(number.split(".")[1])
        factor = rng.choice([0.5, 0.8, 1.25, 2.0])
        return f"{value * factor:.{places}f}"
    return str(int(value) + rng.choice([-1, 1, 2]) if abs(value) < 20 else int(value * rng.choice([0.9, 1.1])))


def change_constant(snapshot: Snapshot, rng) -> PRSpec | None:
    options = [(p, node, name) for p in interesting_sources(snapshot) for node, name in _constants(snapshot.read(p))]
    if not options:
        return None
    path, node, name = rng.choice(options)
    source = snapshot.read(path)
    lines = source.split("\n")
    line = lines[node.lineno - 1]
    numbers = list(re.finditer(r"(?<![\w.])-?\d+(?:\.\d+)?", line.split("=", 1)[1]))
    if not numbers:
        return None
    match = rng.choice(numbers)
    offset = len(line.split("=", 1)[0]) + 1
    new_number = _nudge(match.group(0), rng)
    if new_number == match.group(0):
        return None
    lines[node.lineno - 1] = line[: offset + match.start()] + new_number + line[offset + match.end() :]
    updated = "\n".join(lines)
    if not compiles(updated):
        return None
    return PRSpec("", "", "", "C5", f"Update {name}", f"Policy update: {name} changes from {match.group(0)} to {new_number}.",
                  {path: updated}, "change_constant", {"path": path, "constant": name, "old": match.group(0), "new": new_number})


def change_requirement(snapshot: Snapshot, rng) -> PRSpec | None:
    source = snapshot.read("requirements.txt")
    if not source:
        return None
    lines = source.split("\n")
    pinned = [i for i, l in enumerate(lines) if re.match(r"^[A-Za-z0-9_.-]+>=\d", l)]
    if not pinned:
        return None
    i = rng.choice(pinned)
    package, version = lines[i].split(">=")
    major, *rest = version.split(".")
    bumped = ".".join([major] + [str(int(rest[0]) + 1)] + rest[1:]) if rest else str(int(major) + 1)
    lines[i] = f"{package}>={bumped}"
    return PRSpec("", "", "", "C5", f"Bump {package} to {bumped}", "Dependency refresh.", {"requirements.txt": "\n".join(lines)},
                  "bump_requirement", {"package": package, "from": version, "to": bumped})


def generate(snapshot: Snapshot, count: int, seed: str) -> list[PRSpec]:
    rng = rng_for(seed, "C5")
    specs, attempts = [], 0
    while len(specs) < count and attempts < count * 10:
        attempts += 1
        spec = change_requirement(snapshot, rng) if attempts % 4 == 0 else change_constant(snapshot, rng)
        if spec and all(spec.edits != s.edits for s in specs):
            specs.append(spec)
    return specs
