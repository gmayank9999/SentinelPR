"""Observability hooks for a pipeline run.

The pipeline reports what it does through an ``Emit`` callback: stage starts and ends,
sub-steps inside a stage (test execution, mutants) and LLM calls. Nothing here changes what
the pipeline computes; with no callback attached every hook is a no-op.

``STAGES`` and ``EDGES`` describe the graph for the UI. ``tests/test_service.py`` checks the
pipeline part of them against the compiled LangGraph so they cannot drift apart.
"""

from __future__ import annotations

from typing import Callable

# emit(stage, kind, status, message, data=None)
Emit = Callable[..., None]


def no_emit(*_args, **_kwargs) -> None:
    return None


class RunCancelled(Exception):
    """Raised between pipeline stages when the user cancelled the run."""


# id, label, group, what it does. ``group`` places the stage in the UI: service stages run
# before the LangGraph pipeline, ``post`` stages happen after the decision.
STAGES: list[dict] = [
    {"id": "intake", "label": "Intake", "group": "service",
     "about": "Resolve the base and head revisions and prepare the checkout."},
    {"id": "index", "label": "Repository index", "group": "service",
     "about": "Index the base revision: history, SZZ links, per-test coverage map, retrieval index (cached by SHA)."},
    {"id": "guard_in", "label": "Input guard", "group": "pipeline",
     "about": "Size limits, secret scan and prompt-injection scan of the PR text and added code."},
    {"id": "parse_diff", "label": "Diff parsing", "group": "pipeline",
     "about": "Map changed lines to the functions, methods and classes they belong to."},
    {"id": "impact", "label": "Impact agent", "group": "pipeline",
     "about": "Claim which tests, modules and APIs the change affects, from coverage, call paths and retrieval."},
    {"id": "historian", "label": "Historian agent", "group": "pipeline",
     "about": "Link changed functions to past bug fixes (SZZ) and the issues and PRs that explain them."},
    {"id": "signals", "label": "Signals agent", "group": "pipeline",
     "about": "Change metrics: size, diffusion, ownership, churn, complexity, defect history, change intent."},
    {"id": "select_tests", "label": "Test selection", "group": "pipeline",
     "about": "Choose the tests to run: those covering the changed lines plus those named in impact claims."},
    {"id": "verify", "label": "Verification", "group": "pipeline",
     "about": "Run the selected tests with per-test coverage, mutate the changed lines, and check every claim."},
    {"id": "reconcile", "label": "Reconcile claims", "group": "pipeline",
     "about": "Count VERIFIED / REFUTED / UNVERIFIABLE claims; ask the Impact agent once more if over half were refuted."},
    {"id": "impact_retry", "label": "Impact retry", "group": "pipeline",
     "about": "Self-correction: the Impact agent revises refuted claims using the execution evidence."},
    {"id": "score", "label": "Risk model", "group": "pipeline",
     "about": "Turn the verified evidence into a calibrated defect probability."},
    {"id": "decide", "label": "Policy", "group": "pipeline",
     "about": "PASS, CANARY or BLOCK from the risk and the thresholds; verified test failures always block."},
    {"id": "guard_out", "label": "Output guard", "group": "pipeline",
     "about": "Recompute the decision independently and check that LLM text cannot have changed it."},
    {"id": "publish_rejected", "label": "Rejected", "group": "pipeline",
     "about": "Input guard rejected the PR (secrets or oversize diff): BLOCK, nothing sent to an LLM."},
    {"id": "publish", "label": "Publish to GitHub", "group": "post",
     "about": "Post the Check Run and PR comment. Only when requested for this run and enabled on the server."},
    {"id": "canary", "label": "Canary rollout", "group": "post",
     "about": "After merge: shift traffic 10→25→50→100% and roll back on the first SLO breach."},
]

EDGES: list[tuple[str, str]] = [
    ("intake", "index"), ("index", "guard_in"),
    ("guard_in", "parse_diff"), ("guard_in", "publish_rejected"),
    ("parse_diff", "impact"), ("parse_diff", "historian"), ("parse_diff", "signals"),
    ("impact", "select_tests"), ("historian", "select_tests"), ("signals", "select_tests"),
    ("select_tests", "verify"), ("verify", "reconcile"),
    ("reconcile", "impact_retry"), ("reconcile", "score"), ("impact_retry", "score"),
    ("score", "decide"), ("decide", "guard_out"),
    ("guard_out", "publish"), ("guard_out", "canary"),
]

PIPELINE_NODES = {s["id"] for s in STAGES if s["group"] == "pipeline"}

# Why a stage that did not run was skipped, for the UI.
SKIP_REASONS = {
    "publish": "Publishing to GitHub was not requested for this run. It is opt-in per run and must be enabled on the server.",
    "impact_retry": "Not needed: at most half of the Impact claims were refuted, or the agent ran without an LLM.",
    "publish_rejected": "The input guard accepted the PR.",
    "canary": "Canary rollouts run after a merge to the default branch; this analysis does not deploy anything.",
}
