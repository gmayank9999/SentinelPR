"""The SentinelPR pipeline as a LangGraph state machine.

    guard_in ─► parse_diff ─► ┬─► impact ────┐
                              ├─► historian ─┼─► select_tests ─► verify ─► reconcile ─► score ─► decide ─► guard_out ─► publish
                              └─► signals ───┘                     ▲           │
                                                                   └─ impact_retry (once, if > 50% of impact claims refuted)
    guard_in ─► publish_rejected   (secrets found or diff beyond hard limits)

Impact, Historian and Signals run in parallel. Every node is timed and its token use
recorded in ``trace``, which drives the dashboard's live pipeline view.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from sentinel import __version__, gitutil
from sentinel.agents.base import AgentContext, PullRequest
from sentinel.agents.historian import HistorianAgent, HistoryResult
from sentinel.agents.impact import ImpactAgent, ImpactResult
from sentinel.agents.signals import SignalsAgent, SignalsResult
from sentinel.agents.test_selector import Selection, select_tests
from sentinel.config import PACKAGE_DIR, Config
from sentinel.guards.input import InputGuardResult, scan_inputs
from sentinel.guards.output import check_decision_integrity
from sentinel.models import Claim, GuardEvent, RiskAssessment, TraceEvent, Verdict
from sentinel.orchestrator.state import SentinelState
from sentinel.retrieval.diff_parser import DiffContext
from sentinel.risk.explain import llm_explanation, template_explanation
from sentinel.risk.features import build_features
from sentinel.risk.model import RiskModel
from sentinel.risk.policy import PolicyDecision, decide
from sentinel.verify.reconciler import Reconciliation, reconcile
from sentinel.verify.verifier import VerificationResult, Verifier

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Mutable, rich-typed results shared by the nodes of one run."""

    ctx: AgentContext
    guard: InputGuardResult | None = None
    impact: ImpactResult | None = None
    history: HistoryResult | None = None
    signals: SignalsResult | None = None
    selection: Selection | None = None
    verification: VerificationResult | None = None
    reconciliation: Reconciliation | None = None
    assessment: RiskAssessment | None = None
    policy: PolicyDecision | None = None
    explanation: str = ""
    explanation_source: str = "template"
    retry_claims: list[Claim] = field(default_factory=list)
    retried: bool = False

    @property
    def claims(self) -> list[Claim]:
        impact = self.retry_claims or (self.impact.claims if self.impact else [])
        return impact + (self.history.claims if self.history else [])


class Pipeline:
    def __init__(self, cfg: Config, ctx: AgentContext, *, impact_mode: str | None = None, model: RiskModel | None = None,
                 use_verification: bool = True, use_history: bool = True):
        self.cfg = cfg
        self.rt = Runtime(ctx)
        self.impact_agent = ImpactAgent(impact_mode or cfg.get("agents.impact.mode", "auto"))
        self.historian = HistorianAgent()
        self.signals_agent = SignalsAgent()
        self.model = model or RiskModel.load(model_path(cfg))
        self.use_verification = use_verification
        self.use_history = use_history
        old_sources = {c.path: ctx.read_old(c.old_path or c.path) for c in ctx.changes if c.status in ("modified", "renamed") and c.path.endswith(".py")}
        self.verifier = Verifier(cfg, cfg.project_root, ctx.index.sources, old_sources)

    # tracing ----------------------------------------------------------------------
    def _traced(self, name, fn):
        def node(state: SentinelState) -> dict:
            llm = self.rt.ctx.llm
            tokens_before, calls_before = llm.tokens_used, len(llm.calls)
            started = time.time()
            status, note = "ok", ""
            try:
                update = fn(state) or {}
            except Exception as exc:
                log.exception("node %s failed", name)
                status, note, update = "error", f"{type(exc).__name__}: {exc}", {}
                self.rt.ctx.guard("pipeline", "mid", "warn", f"{name} failed: {note}")
            calls = llm.calls[calls_before:]
            event = TraceEvent(
                node=name, start=started, end=time.time(), status=status,
                tokens=llm.tokens_used - tokens_before,
                model=", ".join(sorted({f"{c.provider}:{c.model}" for c in calls})) or None,
                retries=sum(1 for c in calls if c.error),
                note=note or update.pop("_note", ""),
            )
            update["trace"] = [event.model_dump()]
            update["budget_tokens_left"] = llm.budget_left
            return update
        return node

    # nodes ------------------------------------------------------------------------
    def guard_in(self, state: SentinelState) -> dict:
        ctx = self.rt.ctx
        added = {}
        for change in ctx.changes:
            text = ctx.read_new(change.path) if change.status != "removed" and not change.is_binary else None
            if text:
                lines = text.splitlines()
                added[change.path] = "\n".join(lines[n - 1] for n in change.added_lines if 0 < n <= len(lines))
        result = scan_inputs(ctx.pr.title, ctx.pr.body, ctx.pr.commit_messages, added, ctx.diff_lines, len(ctx.changes), self.cfg)
        self.rt.guard = result
        ctx.guard_events.extend(result.events)
        ctx.pr.title, ctx.pr.body = result.title, result.body  # agents only ever see sanitised text
        return {"rejected": result.rejected, "guard_flags": [f"{e.guard}:{e.severity}" for e in result.events],
                "pr": {"number": ctx.pr.number, "title": result.title, "base_sha": ctx.pr.base_sha, "head_sha": ctx.pr.head_sha},
                "_note": result.reject_reason}

    def parse_diff(self, state: SentinelState) -> dict:
        units = self.rt.ctx.units
        return {"change_units": [u.model_dump() for u in units], "_note": f"{len(units)} change unit(s)"}

    def impact(self, state: SentinelState) -> dict:
        self.rt.impact = self.impact_agent.run(self.rt.ctx)
        return {"impact_claims": [c.model_dump() for c in self.rt.impact.claims], "_note": f"{self.rt.impact.mode}: {len(self.rt.impact.claims)} claims"}

    def historian_node(self, state: SentinelState) -> dict:
        if not self.use_history:
            self.rt.history = HistoryResult([], [], {})
            return {"history_claims": [], "_note": "disabled"}
        self.rt.history = self.historian.run(self.rt.ctx)
        return {"history_claims": [c.model_dump() for c in self.rt.history.claims], "_note": f"{len(self.rt.history.claims)} claims"}

    def signals_node(self, state: SentinelState) -> dict:
        self.rt.signals = self.signals_agent.run(self.rt.ctx)
        return {"signals": self.rt.signals.features, "_note": f"intent={self.rt.signals.intent}"}

    def select(self, state: SentinelState) -> dict:
        rt = self.rt
        rt.selection = select_tests(rt.ctx, rt.impact.evidence if rt.impact else None, rt.impact.claims if rt.impact else [])
        return {"selected_tests": rt.selection.test_ids, "_note": f"{len(rt.selection.test_ids)}/{rt.selection.total_available} tests"}

    def verify(self, state: SentinelState) -> dict:
        rt, ctx = self.rt, self.rt.ctx
        if not self.use_verification:
            return {"verdicts": [], "_note": "verification disabled"}
        rt.verification = self.verifier.verify(
            ctx.units, rt.claims, rt.selection.test_ids if rt.selection else [],
            graph=ctx.graph, store=ctx.store, llm=ctx.llm, llm_allowed=ctx.llm_allowed,
        )
        v = rt.verification
        return {"verdicts": [x.model_dump() for x in v.verdicts],
                "_note": f"{len(v.test_run.passed)} passed, {len(v.test_run.failed)} failed, mutation {v.mutation.score}"}

    def reconcile_node(self, state: SentinelState) -> dict:
        rt = self.rt
        verdicts = rt.verification.verdicts if rt.verification else []
        rt.reconciliation = reconcile(rt.claims, verdicts, retried=rt.retried)
        return {"_note": f"{rt.reconciliation.counts}; impact refuted {rt.reconciliation.impact_refuted_ratio:.0%}"}

    def impact_retry(self, state: SentinelState) -> dict:
        """Self-correction: re-ask the Impact agent with the refutation evidence, then re-check."""
        rt, ctx = self.rt, self.rt.ctx
        rt.retried = True
        retry = self.impact_agent.run(ctx, feedback=rt.reconciliation.feedback)
        rt.retry_claims = retry.claims
        history_claims = rt.history.claims if rt.history else []
        new_verdicts = self.verifier.recheck(rt.verification, ctx.units, retry.claims, graph=ctx.graph, store=ctx.store, llm=ctx.llm, llm_allowed=ctx.llm_allowed)
        kept = [v for v in rt.verification.verdicts if v.claim_id in {c.claim_id for c in history_claims}]
        rt.verification.verdicts = new_verdicts + kept
        return {"impact_claims": [c.model_dump() for c in retry.claims], "impact_retried": True, "_note": f"{len(retry.claims)} revised claims"}

    def score(self, state: SentinelState) -> dict:
        rt = self.rt
        verdicts = rt.verification.verdicts if rt.verification else None
        features = build_features(
            rt.signals.features if rt.signals else {},
            rt.verification,
            rt.claims if verdicts is not None else None,
            verdicts,
            rt.impact.llm_risk if rt.impact else None,
            rt.guard.injection_attempts if rt.guard else 0,
        )
        rt.assessment = self.model.assess(features)
        return {"risk": rt.assessment.probability, "_note": f"risk {rt.assessment.probability:.2f} ({rt.assessment.model})"}

    def decide_node(self, state: SentinelState) -> dict:
        rt = self.rt
        thresholds = self.model.thresholds if self.model.metadata.get("evaluation") else self.cfg.get("risk.thresholds", self.model.thresholds)
        failures = len(rt.verification.test_run.failed) if rt.verification else 0
        rt.policy = decide(rt.assessment.probability, failures, thresholds)
        rt.explanation = template_explanation(rt.assessment)
        ctx = rt.ctx
        if ctx.llm_allowed:
            verified = {v.claim_id for v in (rt.verification.verdicts if rt.verification else []) if v.status.value == "VERIFIED"}
            extra = {e: c.assertion[:100] for c in (rt.history.claims if rt.history else []) if c.claim_id in verified for e in c.evidence_ids}
            text, events = llm_explanation(ctx.llm, rt.assessment, rt.policy.decision, extra)
            ctx.guard_events.extend(events)
            if text:
                rt.explanation, rt.explanation_source = text, "llm"
        return {"decision": rt.policy.decision, "risk_explanation": rt.explanation, "_note": rt.policy.reason}

    def guard_out(self, state: SentinelState) -> dict:
        rt = self.rt
        failures = len(rt.verification.test_run.failed) if rt.verification else 0
        expected = decide(rt.assessment.probability, failures, rt.policy.thresholds).decision
        events = check_decision_integrity(state.get("decision"), expected, rt.explanation if rt.explanation_source == "llm" else "")
        rt.ctx.guard_events.extend(events)
        update = {"guard_flags": [f"{e.guard}:{e.severity}" for e in events]}
        if state.get("decision") != expected:
            rt.policy = PolicyDecision(expected, rt.policy.reason, rt.policy.thresholds)
            update["decision"] = expected
        return update

    def publish_rejected(self, state: SentinelState) -> dict:
        self.rt.policy = PolicyDecision("BLOCK", self.rt.guard.reject_reason, self.cfg.get("risk.thresholds", {}))
        return {"decision": "BLOCK", "_note": "rejected by input guard"}

    # graph --------------------------------------------------------------------------
    def build(self):
        g = StateGraph(SentinelState)
        nodes = {
            "guard_in": self.guard_in, "parse_diff": self.parse_diff, "impact": self.impact,
            "historian": self.historian_node, "signals": self.signals_node, "select_tests": self.select,
            "verify": self.verify, "reconcile": self.reconcile_node, "impact_retry": self.impact_retry,
            "score": self.score, "decide": self.decide_node, "guard_out": self.guard_out,
            "publish_rejected": self.publish_rejected,
        }
        for name, fn in nodes.items():
            g.add_node(name, self._traced(name, fn))
        g.add_edge(START, "guard_in")
        g.add_conditional_edges("guard_in", lambda s: "publish_rejected" if s.get("rejected") else "parse_diff",
                                {"publish_rejected": "publish_rejected", "parse_diff": "parse_diff"})
        for agent in ("impact", "historian", "signals"):
            g.add_edge("parse_diff", agent)
        g.add_edge(["impact", "historian", "signals"], "select_tests")
        g.add_edge("select_tests", "verify")
        g.add_edge("verify", "reconcile")
        g.add_conditional_edges(
            "reconcile",
            lambda s: "impact_retry" if self.rt.reconciliation and self.rt.reconciliation.should_retry_impact and self.rt.impact and self.rt.impact.mode == "llm" else "score",
            {"impact_retry": "impact_retry", "score": "score"},
        )
        g.add_edge("impact_retry", "score")
        g.add_edge("score", "decide")
        g.add_edge("decide", "guard_out")
        g.add_edge("guard_out", END)
        g.add_edge("publish_rejected", END)
        return g.compile()

    def run(self) -> dict:
        started = time.time()
        final = self.build().invoke({"guard_flags": [], "trace": [], "budget_tokens_left": self.rt.ctx.llm.budget_left})
        return build_report(self, final, time.time() - started)


def model_path(cfg: Config) -> Path:
    """The analysed repository may ship its own model; otherwise use the one bundled with SentinelPR."""
    configured = Path(cfg.get("risk.model_path", "sentinel/risk/artifacts/model.json"))
    if configured.is_absolute():
        return configured
    in_repo = cfg.repo_root / configured
    return in_repo if in_repo.exists() else PACKAGE_DIR.parent / configured


def build_report(pipeline: Pipeline, state: dict, elapsed: float) -> dict:
    rt, ctx, cfg = pipeline.rt, pipeline.rt.ctx, pipeline.cfg
    claims = rt.claims
    verdicts = rt.verification.verdicts if rt.verification else []
    status = {v.claim_id: v.status.value for v in verdicts}
    report = {
        "schema": "sentinelpr.run/1",
        "run_id": f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sentinel_version": __version__,
        "repository": cfg.get("publish.repository") or "",
        "project_root": cfg.get("project.root"),
        "pr": {"number": ctx.pr.number, "title": ctx.pr.title, "author": ctx.pr.author,
               "base_sha": ctx.pr.base_sha, "head_sha": ctx.pr.head_sha, "is_fork": ctx.pr.is_fork},
        "rejected": bool(state.get("rejected")),
        "decision": (rt.policy.decision if rt.policy else state.get("decision")) or "BLOCK",
        "decision_reason": rt.policy.reason if rt.policy else "",
        "thresholds": rt.policy.thresholds if rt.policy else {},
        "risk": rt.assessment.probability if rt.assessment else None,
        "risk_model": rt.assessment.model if rt.assessment else None,
        "calibrated": rt.assessment.calibrated if rt.assessment else False,
        "contributions": [c.model_dump() for c in rt.assessment.contributions] if rt.assessment else [],
        "features": rt.assessment.features if rt.assessment else {},
        "explanation": rt.explanation,
        "explanation_source": rt.explanation_source,
        "changes": [c.model_dump() for c in ctx.changes],
        "change_units": [u.model_dump() for u in ctx.units],
        "impact_mode": rt.impact.mode if rt.impact else None,
        "impact_retried": rt.retried,
        "llm_risk": rt.impact.llm_risk if rt.impact else None,
        "claims": [{**c.model_dump(), "status": status.get(c.claim_id, "UNCHECKED")} for c in claims],
        "verdicts": [v.model_dump(mode="json") for v in verdicts],
        "verdict_counts": rt.reconciliation.counts if rt.reconciliation else {},
        "selection": {
            "tests": rt.selection.test_ids, "reasons": rt.selection.reasons, "total_available": rt.selection.total_available,
            "estimated_s": rt.selection.estimated_s, "full_suite_s": rt.selection.full_suite_s,
            "time_saved_ratio": round(rt.selection.time_saved_ratio, 4),
        } if rt.selection else None,
        "verification": ({**rt.verification.to_dict(), "behaviour_preserving_units": pipeline.verifier.preserved_units}
                         if rt.verification else None),
        "signals": rt.signals.features if rt.signals else {},
        "intent": {"label": rt.signals.intent, "source": rt.signals.intent_source} if rt.signals else None,
        "history": {"memories": rt.history.memories, "features": rt.history.features, "documents": rt.history.documents} if rt.history else None,
        "guard_events": [e.model_dump() for e in ctx.guard_events],
        "trace": state.get("trace", []),
        "llm": {**ctx.llm.usage(), "calls_detail": [c.model_dump() for c in ctx.llm.calls], "available": ctx.llm_allowed},
        "prompts": ctx.prompt_versions,
        "retrieval": {"mode": cfg.get("retrieval.mode"), "top_k": cfg.get("retrieval.top_k"), "queries": getattr(ctx.retriever, "queries", [])[:20]},
        "elapsed_s": round(elapsed, 2),
    }
    report["tokens"] = report["llm"]["tokens"]
    return report


def make_context(cfg: Config, *, base: str, head: str | None, pr: PullRequest, store, index, llm) -> AgentContext:
    """Diff the change and assemble the agent context."""
    from sentinel.retrieval.hybrid import HybridRetriever

    diff = DiffContext(cfg.repo_root, base, head, cfg.project_prefix)
    changes = diff.file_changes()
    units = diff.change_units(changes)
    name_only = ["diff", "--name-only", base] + ([head] if head else [])
    all_paths = [p for p in gitutil.git(cfg.repo_root, *name_only).splitlines() if p]
    return AgentContext(cfg, store, index, HybridRetriever(index), llm, pr, changes, units, diff=diff, all_changed_paths=all_paths)
