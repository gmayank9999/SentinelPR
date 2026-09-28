"""Signals Agent: hard numbers about the change (classic just-in-time defect-prediction metrics).

Everything here is deterministic except the change-intent label, for which the LLM is used
when available (with a keyword heuristic otherwise).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel

from sentinel.agents.base import AgentContext
from sentinel.agents.prompts import load_prompt
from sentinel.retrieval.graph import is_test_path

log = logging.getLogger(__name__)

DAY = 86400
RECENT_WINDOW_DAYS = 90
INTENTS = ("refactor", "feature", "bugfix", "config", "test", "docs")

DEPENDENCY_FILES = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|Pipfile(\.lock)?|poetry\.lock|package(-lock)?\.json)$")
CI_FILES = re.compile(r"(^|/)(\.github/|\.gitlab-ci\.yml|Dockerfile|docker-compose[^/]*\.ya?ml|Makefile|action\.ya?ml)")
CONFIG_FILES = re.compile(r"\.(ya?ml|ini|cfg|toml|json|env)$|(^|/)\.env")
DOC_FILES = re.compile(r"\.(md|rst|txt)$|(^|/)docs/", re.IGNORECASE)


class _Intent(BaseModel):
    intent: str


@dataclass
class SignalsResult:
    features: dict[str, float]
    intent: str
    intent_source: str
    details: dict = field(default_factory=dict)


def change_entropy(changes) -> float:
    """Normalised Shannon entropy of modified lines across files (Hassan, 2009)."""
    sizes = [c.additions + c.deletions for c in changes if c.additions + c.deletions > 0]
    total = sum(sizes)
    if len(sizes) <= 1 or total == 0:
        return 0.0
    entropy = -sum((s / total) * math.log2(s / total) for s in sizes)
    return round(entropy / math.log2(len(sizes)), 4)


def unit_complexity(source: str | None) -> dict[str, int]:
    """Cyclomatic complexity per qualified name, via radon."""
    if not source:
        return {}
    try:
        from radon.complexity import cc_visit
    except ImportError:
        return {}
    try:
        blocks = cc_visit(source)
    except SyntaxError:
        return {}
    result: dict[str, int] = {}

    def walk(block, prefix: str = "") -> None:
        name = f"{prefix}{block.name}"
        result[name] = block.complexity
        for method in getattr(block, "methods", []) or []:
            result[f"{name}.{method.name}"] = method.complexity
        for inner in getattr(block, "closures", []) or []:
            walk(inner, f"{name}.")

    for block in blocks:
        if getattr(block, "is_method", False):
            continue  # reported through its class
        walk(block)
    return result


def heuristic_intent(title: str, paths: list[str]) -> str:
    if paths and all(is_test_path(p) for p in paths):
        return "test"
    if paths and all(DOC_FILES.search(p) for p in paths):
        return "docs"
    if paths and all(CONFIG_FILES.search(p) or DEPENDENCY_FILES.search(p) or CI_FILES.search(p) for p in paths):
        return "config"
    lowered = title.lower()
    if re.search(r"\b(fix|bug|crash|regression|wrong|incorrect|broken)\b", lowered):
        return "bugfix"
    if re.search(r"\b(refactor|rename|clean ?up|tidy|simplify|extract|move|reformat)\b", lowered):
        return "refactor"
    return "feature"


class SignalsAgent:
    name = "signals"

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    def run(self, ctx: AgentContext) -> SignalsResult:
        store, graph = ctx.store, ctx.graph
        changes = ctx.changes
        project_paths = [c.path for c in changes]
        repo_paths = ctx.all_changed_paths or project_paths
        source_changes = [c for c in changes if c.path.endswith(".py") and not is_test_path(c.path)]

        commits = store.commits()
        reference_time = max((c["timestamp"] for c in commits), default=0)
        author = ctx.pr.author.lower()

        # Ownership and history of the touched files ------------------------------
        prior_by_author = 0
        ages, recent, file_authors = [], 0, set()
        for change in source_changes:
            history = store.file_history(change.old_path or change.path)
            if history:
                ages.append((reference_time - history[-1]["timestamp"]) / DAY)
            for row in history:
                file_authors.add(row["author"])
                if author and row["author"].lower() == author:
                    prior_by_author += 1
                if reference_time - row["timestamp"] <= RECENT_WINDOW_DAYS * DAY:
                    recent += 1

        # Complexity of changed units, before and after ---------------------------
        complexity_delta, max_complexity = 0, 0
        for change in source_changes:
            old = unit_complexity(ctx.read_old(change.old_path or change.path) if change.status != "added" else None)
            new = unit_complexity(ctx.read_new(change.path))
            for unit in (u for u in ctx.units if u.file == change.path and u.kind != "module"):
                after = new.get(unit.qualname, 0)
                before = old.get(unit.qualname, 0)
                complexity_delta += after - before
                max_complexity = max(max_complexity, after)

        # Defect history (SZZ) and test reach of the changed units -----------------
        szz_defects, tested_lines, changed_lines = 0, 0, 0
        for unit in ctx.source_units:
            if unit.kind != "module":
                szz_defects += len({link["fix_sha"] for link in store.szz_links(unit.file, unit.qualname)})
            lines = len(unit.changed_lines) + len(unit.removed_lines) or 1
            changed_lines += lines
            if unit.kind != "module" and graph.covering_tests(ctx.node(unit)):
                tested_lines += lines

        added = sum(c.additions for c in changes)
        deleted = sum(c.deletions for c in changes)
        features = {
            "lines_added": float(added),
            "lines_deleted": float(deleted),
            "log_churn": round(math.log1p(added + deleted), 4),
            "files_touched": float(len(changes)),
            "source_files_touched": float(len(source_changes)),
            "change_units": float(len(ctx.source_units)),
            "entropy": change_entropy(changes),
            "author_prior_commits": float(prior_by_author),
            "author_is_new": float(prior_by_author == 0),
            "distinct_file_authors": float(len(file_authors)),
            "code_age_days": round(sum(ages) / len(ages), 1) if ages else 0.0,
            "recent_churn": float(recent),
            "complexity_delta": float(complexity_delta),
            "max_complexity": float(max_complexity),
            "szz_defects": float(szz_defects),
            "units_with_tests_ratio": round(tested_lines / changed_lines, 4) if changed_lines else 1.0,
            "touches_dependencies": float(any(DEPENDENCY_FILES.search(p) for p in repo_paths)),
            "touches_ci": float(any(CI_FILES.search(p) for p in repo_paths)),
            "touches_config": float(any(CONFIG_FILES.search(p) and not DEPENDENCY_FILES.search(p) and not CI_FILES.search(p) for p in repo_paths)),
            "signature_changes": float(sum(1 for u in ctx.units if u.signature_changed)),
            "test_only": float(bool(project_paths) and all(is_test_path(p) for p in project_paths)),
            "docs_only": float(bool(repo_paths) and all(DOC_FILES.search(p) for p in repo_paths)),
            "tests_changed": float(any(is_test_path(p) for p in project_paths)),
        }

        intent, source = heuristic_intent(ctx.pr.title, repo_paths), "heuristic"
        if self.use_llm and ctx.llm_allowed:
            try:
                prompt = load_prompt("intent", ctx.cfg.get("prompts.intent"))
                ctx.prompt_versions["intent"] = prompt.id
                system, user = prompt.render(
                    title=ctx.pr.title, body=ctx.pr.body[:800],
                    files=", ".join(repo_paths[:20]), symbols=", ".join(u.qualname for u in ctx.units[:20]),
                )
                parsed, _, _ = ctx.llm.structured("intent", system, user, _Intent)
                if parsed and parsed.intent.lower() in INTENTS:
                    intent, source = parsed.intent.lower(), "llm"
            except Exception as exc:
                log.info("intent classification fell back to heuristic: %s", exc)
        for name in INTENTS:
            features[f"intent_{name}"] = float(intent == name)
        return SignalsResult(features, intent, source, {"file_authors": sorted(file_authors), "reference_time": reference_time})

