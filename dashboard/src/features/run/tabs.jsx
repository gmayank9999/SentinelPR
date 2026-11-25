import { Link } from "react-router-dom";
import { CheckCircle2, ExternalLink, GitCompare, Info, ListChecks, TriangleAlert, XCircle } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { ago, duration, num, pct, short, stamp } from "../../lib/format.js";
import { stageStatus } from "../../lib/runModel.js";
import { Async, Banner, Button, Card, DecisionBadge, EmptyState, KV, RiskMeter, RunStatus, StageIcon, Stat, cx } from "../../ui.jsx";
import LlmUsage from "./LlmUsage.jsx";
import ModelCard from "./ModelCard.jsx";
import { annotateContributions, findings, nextActions, verifiedImpact } from "./derive.js";
import { ClaimsTable, ContributionBars, Disclosure, EvidenceChip, FeatureTable, MutantsTable, Tiles, TestsList, coverageText, evidenceKind } from "./parts.jsx";

const TONE = { bad: [XCircle, "text-bad"], warn: [TriangleAlert, "text-warn"], ok: [CheckCircle2, "text-ok"], info: [Info, "text-brand"] };

/** The explanation, with its [feat:…] / [issue:…] citations shown as small evidence tags instead of raw brackets. */
function Explanation({ text }) {
  if (!text) return <p className="text-sm text-muted">No explanation was produced.</p>;
  const parts = text.split(/(\[[a-z]+:[^\]]+\])/g);
  return (
    <p className="text-sm leading-relaxed text-ink">
      {parts.map((part, i) => (/^\[[a-z]+:[^\]]+\]$/.test(part)
        ? <span key={i} className="mono ml-0.5 rounded bg-s3 px-1 text-[10px] text-faint" title="evidence this sentence cites">{part.slice(1, -1)}</span>
        : part))}
    </p>
  );
}

export function SummaryTab({ report, meta }) {
  const r = report;
  const foreign = meta && meta.repo_id !== "unierp-demo";
  const v = r.verification;
  const impact = verifiedImpact(r);
  const acts = nextActions(r);
  return (
    <div className="space-y-6">
      {foreign && <Banner tone="warn" title="Read this decision as a prompt for review, not a verdict">The risk model and its thresholds were calibrated on the bundled UniERP benchmark. On real repositories in the evaluation the
        false-block rate was above the 5% target and the thresholds did not transfer cleanly (see Risk Analysis). The test, coverage and mutation results above are measured on this repository; the risk number is not.
        {meta.execution === "docker" ? " Tests ran in a Docker sandbox." : " Tests ran on the host, unsandboxed."}</Banner>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex flex-wrap items-center gap-4"><DecisionBadge decision={r.decision} size="lg" /><span className="text-sm text-muted">{r.decision_reason}</span></div>
          <div className="mt-5 max-w-xl"><RiskMeter risk={r.risk} thresholds={r.thresholds} /></div>
          <div className="mt-5 border-t border-line pt-4">
            <Explanation text={r.explanation} />
            <p className="mt-2 text-xs text-faint">{r.explanation_source === "llm" ? "Written by an LLM; every sentence must cite evidence the system holds, and the text cannot change the decision." : "Generated from the model's feature contributions (no LLM involved)."}</p>
          </div>
        </Card>
        <Card title="Verification at a glance">
          <Tiles items={[
            ["Tests", v ? `${v.tests.passed}/${v.tests.selected}` : "—", v ? `${v.tests.failed.length} failing` : "", v?.tests.failed.length ? "text-bad" : ""],
            ["Changed lines run", v ? `${v.changed_lines.covered}/${v.changed_lines.total}` : "—", v ? `${pct(v.changed_lines.coverage)} executed` : ""],
            ["Mutation score", v?.mutation.score != null ? pct(v.mutation.score) : "Not measured", v ? `${(v.mutation.mutants || []).filter((m) => m.status === "survived").length} survived` : ""],
            ["Claims verified", `${r.verdict_counts?.VERIFIED || 0}`, `${r.verdict_counts?.REFUTED || 0} refuted · ${r.verdict_counts?.UNVERIFIABLE || 0} unverifiable`],
          ]} />
          {r.selection && <p className="mt-3 text-xs text-muted">Ran {r.selection.tests.length} of {r.selection.total_available} tests, saving {pct(r.selection.time_saved_ratio)} of the suite's time.</p>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Key findings">
          <ul className="space-y-3">
            {findings(r).map((f, i) => { const [Icon, tone] = TONE[f.tone]; return (
              <li key={i} className="flex gap-3"><Icon size={16} className={cx("mt-0.5 shrink-0", tone)} aria-hidden /><div><div className="text-sm text-ink">{f.title}</div>{f.detail && <div className="text-xs text-muted">{f.detail}</div>}</div></li>); })}
          </ul>
        </Card>
        <Card title="Recommended next actions" action={<ListChecks size={14} className="text-faint" aria-hidden />}>
          {acts.length ? <ol className="list-decimal space-y-2 pl-5 text-sm text-ink marker:text-faint">{acts.map((a, i) => <li key={i}>{a}</li>)}</ol> : <p className="text-sm text-muted">Nothing to do.</p>}
        </Card>
      </div>

      <Card title="Verified impact">
        <div className="grid gap-6 md:grid-cols-3">
          <div><h4 className="mb-2 text-sm font-medium">Modules <span className="text-faint">({impact.modules.length})</span></h4>{impact.modules.length ? <ul className="mono space-y-1">{impact.modules.slice(0, 8).map((c) => <li key={c.claim_id} className="break-all">{c.target}</li>)}</ul> : <p className="text-sm text-muted">No verified module impact.</p>}</div>
          <div><h4 className="mb-2 text-sm font-medium">Tests <span className="text-faint">({impact.tests.length})</span></h4>{impact.tests.length ? <ul className="mono space-y-1">{impact.tests.slice(0, 8).map((c) => <li key={c.claim_id} className="break-all">{c.target.split("::").slice(-1)[0]}</li>)}</ul> : <p className="text-sm text-muted">No verified test impact.</p>}</div>
          <div><h4 className="mb-2 text-sm font-medium">API breaks <span className="text-faint">({impact.apis.length})</span></h4>{impact.apis.length ? <ul className="mono space-y-1">{impact.apis.map((c) => <li key={c.claim_id} className="break-all">{c.target}</li>)}</ul> : <p className="text-sm text-muted">None verified.</p>}</div>
        </div>
      </Card>
    </div>
  );
}

export function DiffImpactTab({ report, repoUrl }) {
  const r = report;
  const link = (path, line) => (repoUrl && r.pr?.head_sha ? `${repoUrl}/blob/${r.pr.head_sha}/${path}${line ? `#L${line}` : ""}` : null);
  const history = (r.claims || []).filter((c) => c.agent === "historian");
  const viaEvidence = (c) => evidenceKind(c.evidence_ids[0]);
  const impactClaims = (r.claims || []).filter((c) => ["test_impact", "module_impact", "api_break"].includes(c.type) && c.status !== "REFUTED");
  const s = r.signals || {};
  return (
    <div className="space-y-6">
      <Card title={`Changed files (${r.changes?.length || 0})`} pad={false}>
        <table className="table"><thead><tr><th>File</th><th>Status</th><th className="text-right">Lines</th></tr></thead><tbody>
          {(r.changes || []).map((c) => (
            <tr key={c.path}><td className="mono break-all">{link(c.path) ? <a className="hover:underline" href={link(c.path)} target="_blank" rel="noreferrer">{c.path} <ExternalLink size={11} className="inline" aria-hidden /></a> : c.path}</td>
              <td className="text-muted">{c.status}</td><td className="mono text-right"><span className="text-ok">+{c.added_lines.length}</span> <span className="text-bad">−{c.removed_lines.length}</span></td></tr>))}
        </tbody></table>
      </Card>
      <Card title={`Changed functions and classes (${r.change_units?.length || 0})`} pad={false}>
        <table className="table"><thead><tr><th>Unit</th><th>Kind</th><th>Change</th><th className="text-right">Changed lines</th></tr></thead><tbody>
          {(r.change_units || []).map((u) => (
            <tr key={u.id}><td className="mono break-all">{u.id}</td><td>{u.kind}</td><td>{u.change_type}{u.new_signature && u.old_signature !== u.new_signature ? <div className="mono text-xs text-warn">{u.old_signature} → {u.new_signature}</div> : null}</td><td className="mono text-right">{u.changed_lines.length}</td></tr>))}
        </tbody></table>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Predicted impact (${impactClaims.length})`}>
          <p className="mb-3 text-xs text-muted"><span className="text-ok">Observed</span> relationships come from recorded coverage or repository history. <span>Inferred</span> ones come from the call graph or retrieved code and are only as good as that analysis.</p>
          <ul className="space-y-2.5">
            {impactClaims.slice(0, 30).map((c) => (
              <li key={c.claim_id} className="text-sm"><div className="flex items-start justify-between gap-2"><span className="mono break-all">{c.target}</span>
                <span className={cx("pill shrink-0", viaEvidence(c).direct ? "bg-ok/10 text-ok" : "bg-s3 text-muted")}>{viaEvidence(c).direct ? "observed" : "inferred"}</span></div>
                <div className="text-xs text-faint">{c.type.replace("_", " ")} · {c.reason}</div></li>))}
          </ul>
        </Card>
        <Card title={`Tests selected (${r.selection?.tests.length || 0} of ${r.selection?.total_available ?? "?"})`}>
          {r.selection ? <TestsList tests={r.selection.tests} reasons={r.selection.reasons} /> : <p className="text-sm text-muted">No test selection was made.</p>}
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Coverage of the changed lines">
          <Tiles items={[["Executed", coverageText(r.verification?.changed_lines)], ["Never executed", r.verification?.changed_lines?.uncovered?.length ?? "—"]]} />
          {(r.verification?.changed_lines?.uncovered || []).length > 0 && <ul className="mono mt-3 space-y-1 text-warn">{r.verification.changed_lines.uncovered.map((u) => <li key={u}>{u}</li>)}</ul>}
        </Card>
        <Card title="Complexity and churn signals">
          <KV rows={[["Change intent", r.intent ? `${r.intent.label} (${r.intent.source})` : "—"], ["Lines added / deleted", s.lines_added != null ? `${s.lines_added} / ${s.lines_deleted}` : s.log_churn != null ? `churn ${num(s.log_churn)} (log)` : "—"],
            ["Files touched", s.files_touched], ["Past bug fixes in touched functions", s.szz_defects], ["Complexity change", s.complexity_delta], ["Max complexity", s.max_complexity], ["Recent churn", s.recent_churn], ["Author new to these files", s.author_is_new != null ? (s.author_is_new ? "yes" : "no") : null]]} />
        </Card>
      </div>
      <Card title={`History and past bugs (${history.length})`}>
        {history.length ? <ul className="space-y-3">{history.map((c) => <li key={c.claim_id} className="text-sm"><div className="flex items-start justify-between gap-2"><span className="mono break-all">{c.target}</span><span className="pill shrink-0 bg-s3 text-muted">{c.status.toLowerCase()}</span></div><div className="text-xs text-muted">{c.reason}</div><div className="mt-1 flex flex-wrap gap-1">{c.evidence_ids.map((e) => <EvidenceChip key={e} id={e} />)}</div></li>)}</ul>
          : <p className="text-sm text-muted">The Historian found no past bug fixes or discussions linked to the changed functions.</p>}
        {r.history?.memories?.length > 0 && <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted">{r.history.memories.map((m, i) => <li key={i}>{m}</li>)}</ul>}
      </Card>
    </div>
  );
}

export function RiskTab({ report }) {
  const r = report;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Calibrated risk" className="lg:col-span-2">
          <div className="mb-4 flex items-baseline gap-3"><span className="text-3xl font-semibold tabular-nums">{r.risk != null ? num(r.risk, 3) : "Not measured"}</span><DecisionBadge decision={r.decision} /></div>
          <div className="max-w-xl"><RiskMeter risk={r.risk} thresholds={r.thresholds} /></div>
          <dl className="mt-5 grid max-w-xl grid-cols-2 gap-3 text-sm text-muted">
            <div><dt className="text-faint">Raw score vs calibrated</dt><dd>The model's raw score is passed through {r.calibrated ? "a fitted Platt calibrator" : "no calibrator"} to give the probability shown. The policy compares that probability with the thresholds.</dd></div>
            <div><dt className="text-faint">Policy</dt><dd>Failing tests always BLOCK. Otherwise risk ≥ {r.thresholds?.block} blocks, risk ≥ {r.thresholds?.canary} routes to canary.</dd></div>
          </dl>
        </Card>
        <Card title="Model used"><KV rows={[["Model", r.risk_model], ["Calibrated", r.calibrated ? "yes" : "no"], ["Impact mode", r.impact_mode], ["LLM rating used", r.llm_risk != null ? `${r.llm_risk} (one feature)` : "none"]]} /></Card>
      </div>
      <Card title="What moved the score"><p className="mb-3 text-xs text-muted">Contribution = the model weight × the feature value, in log-odds. Positive pushes toward defective, negative toward safe. Only the eight largest are kept per run.</p><ContributionBars contributions={annotateContributions(r.contributions, r)} /></Card>
      <Card title="Every feature the model saw" pad={false}><div className="p-1"><FeatureTable features={r.features} contributions={r.contributions} /></div></Card>
      <Card title="About the model"><ModelCard /></Card>
    </div>
  );
}

export function TraceTab({ run, stages, onOpenLive, report, meta }) {
  return (
    <div className="space-y-6">
    <Card title="Pipeline stages" action={<Button size="sm" icon={null} onClick={onOpenLive}>Open Live Execution</Button>} pad={false}>
      <table className="table"><thead><tr><th>Stage</th><th>Status</th><th className="text-right">Duration</th><th className="text-right">Tokens</th><th>Result</th></tr></thead><tbody>
        {stages.map((s) => { const st = stageStatus(run, s); const info = run.stages[s.id]; return (
          <tr key={s.id}><td className="font-medium">{s.label}</td><td><span className="inline-flex items-center gap-1.5"><StageIcon status={st} size={14} />{st}</span></td>
            <td className="mono text-right">{st === "completed" || st === "failed" ? duration(info?.duration) : "—"}</td>
            <td className="mono text-right">{s.group === "pipeline" && st === "completed" ? info?.tokens || 0 : "—"}</td>
            <td className="text-muted">{st === "skipped" ? info?.reason : info?.message || (st === "pending" ? "No telemetry" : "")}</td></tr>); })}
      </tbody></table>
    </Card>
    <LlmUsage report={report} meta={meta} />
    </div>
  );
}

export function HistoryTab({ runs, currentId, repoId }) {
  if (!runs?.length) return <EmptyState icon={GitCompare} title="No other analyses of this pull request" />;
  return (
    <Card title={`Analyses of this pull request (${runs.length})`} pad={false}>
      <ul className="divide-y divide-line">
        {runs.map((m, i) => (
          <li key={m.run_id} className="flex flex-wrap items-center gap-3 px-5 py-3">
            <Link to={`/runs/${m.run_id}`} className="min-w-0 flex-1">
              <span className="flex items-center gap-2 text-sm">{m.decision ? <DecisionBadge decision={m.decision} /> : <RunStatus status={m.status} />}{m.run_id === currentId && <span className="pill bg-brand/15 text-brand">viewing</span>}</span>
              <span className="mono text-xs text-faint">{short(m.pr.head_sha)} · {stamp(m.created_at)} ({ago(m.created_at)}){m.risk != null ? ` · risk ${m.risk.toFixed(2)}` : ""} · {m.options?.llm ? "LLM on" : "no LLM"}</span>
            </Link>
            {runs[i + 1] && m.status === "completed" && runs[i + 1].status === "completed" && <Link to={`/compare?a=${runs[i + 1].run_id}&b=${m.run_id}`}><Button size="sm" icon={GitCompare}>Compare with previous</Button></Link>}
          </li>
        ))}
      </ul>
    </Card>
  );
}
