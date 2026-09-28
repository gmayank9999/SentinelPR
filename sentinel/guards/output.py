"""Output guardrails: citation, decision integrity and patch safety."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field

from sentinel.models import GuardEvent

CITATION = re.compile(r"\[([^\[\]]+)\]")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class CitationResult:
    text: str
    kept: int
    removed: int
    events: list[GuardEvent] = field(default_factory=list)


def enforce_citations(text: str, allowed_ids: set[str], *, drop: bool = True) -> CitationResult:
    """Keep only sentences whose citations are all known evidence ids.

    Sentences with no citation, or citing ids that do not exist, are removed (``drop``) or
    marked ``(unverified)``.
    """
    kept, removed, out, events = 0, 0, [], []
    for sentence in (s.strip() for s in SENTENCE_SPLIT.split(text.strip()) if s.strip()):
        cited = [c.strip() for group in CITATION.findall(sentence) for c in group.split(",")]
        valid = bool(cited) and all(c in allowed_ids for c in cited)
        if valid:
            out.append(sentence)
            kept += 1
            continue
        removed += 1
        reason = "no citation" if not cited else f"unknown evidence {[c for c in cited if c not in allowed_ids][:3]}"
        events.append(GuardEvent(guard="citation", stage="out", severity="info", message=f"sentence dropped ({reason}): {sentence[:90]}"))
        if not drop:
            out.append(f"{CITATION.sub('', sentence).strip()} (unverified)")
    return CitationResult(" ".join(out), kept, removed, events)


def check_decision_integrity(recorded: str, expected: str, llm_text: str = "") -> list[GuardEvent]:
    """The decision must equal what the policy computes from the risk model; LLM text is irrelevant."""
    events = []
    if recorded != expected:
        events.append(GuardEvent(guard="decision-integrity", stage="out", severity="block",
                                 message=f"decision {recorded} does not match policy output {expected}; policy output enforced"))
    claimed = re.findall(r"\b(PASS|CANARY|BLOCK)\b", llm_text or "")
    if any(c != expected for c in claimed):
        events.append(GuardEvent(guard="decision-integrity", stage="out", severity="warn",
                                 message=f"generated text mentioned a different decision ({', '.join(sorted(set(claimed)))}); ignored"))
    return events


# --- patch safety ------------------------------------------------------------------

PROTECTED_PATHS = re.compile(r"(^|/)(\.github/|requirements[^/]*\.txt$|pyproject\.toml$|setup\.(py|cfg)$|Pipfile|poetry\.lock|Dockerfile|docker-compose|Makefile|action\.ya?ml)")
DANGEROUS_CALLS = {"eval": "use of eval()", "exec": "use of exec()", "__import__": "dynamic __import__",
                   "system": "os.system()", "popen": "os.popen()"}


@dataclass
class PatchReview:
    allowed: bool
    events: list[GuardEvent] = field(default_factory=list)


def _module_imports(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def review_patch(files: dict[str, str], originals: dict[str, str | None], project_modules: set[str]) -> PatchReview:
    """Static checks on generated code (a subset of bandit/semgrep rules, dependency-free)."""
    events: list[GuardEvent] = []
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for path, content in files.items():
        if PROTECTED_PATHS.search(path):
            events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message="edits a CI, build or dependency file", location=path))
            continue
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message=f"does not parse: {exc.msg}", location=f"{path}:{exc.lineno}"))
            continue
        before = originals.get(path)
        old_imports = _module_imports(ast.parse(before)) if before else set()
        for name in sorted(_module_imports(tree) - old_imports - stdlib - project_modules):
            events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message=f"introduces a new dependency: {name}", location=path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
                if name in ("eval", "exec", "__import__") or (owner == "os" and name in ("system", "popen")):
                    events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message=DANGEROUS_CALLS[name], location=f"{path}:{node.lineno}"))
                if owner in ("pickle", "marshal") and name == "loads":
                    events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message=f"{owner}.loads on untrusted data", location=f"{path}:{node.lineno}"))
                if owner == "yaml" and name == "load" and not any(k.arg == "Loader" for k in node.keywords):
                    events.append(GuardEvent(guard="patch-safety", stage="out", severity="warn", message="yaml.load without an explicit Loader", location=f"{path}:{node.lineno}"))
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        events.append(GuardEvent(guard="patch-safety", stage="out", severity="block", message="subprocess with shell=True", location=f"{path}:{node.lineno}"))
                    if kw.arg == "verify" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        events.append(GuardEvent(guard="patch-safety", stage="out", severity="warn", message="TLS verification disabled", location=f"{path}:{node.lineno}"))
    return PatchReview(allowed=not any(e.severity == "block" for e in events), events=events)
