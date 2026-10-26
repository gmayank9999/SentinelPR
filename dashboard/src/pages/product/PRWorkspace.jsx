import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Ban, BookOpenCheck, ExternalLink, FileSearch, GaugeCircle, GitBranch, History, LayoutList, Play, RefreshCw, Terminal } from "lucide-react";
import { api, useApi } from "../../lib/api.js";
import { useRun } from "../../lib/useRun.js";
import { analyseLabel } from "./labels.js";
import { ago, duration, repoPath, short, stamp } from "../../lib/format.js";
import { stageStatus } from "../../lib/runModel.js";
import { useAnalyze } from "../../lib/analyze.jsx";
import { useCrumbs } from "../../Shell.jsx";
import { Async, Banner, Button, Card, DecisionBadge, EmptyState, ErrorState, Id, RunStatus, SkeletonRows, StageIcon, Tabs, cx, useMode, useToast } from "../../ui.jsx";
import EvidenceExplorer from "../../features/run/EvidenceExplorer.jsx";
import { DiffImpactTab, HistoryTab, RiskTab, SummaryTab, TraceTab } from "../../features/run/tabs.jsx";
import LiveView from "../../features/live/LiveView.jsx";
import PublishPanel from "../../features/run/PublishPanel.jsx";

const TABS = [
  { id: "summary", label: "Summary", icon: LayoutList },
  { id: "evidence", label: "Evidence", icon: FileSearch },
  { id: "impact", label: "Diff & Impact", icon: GitBranch },
  { id: "trace", label: "Execution Trace", icon: Terminal },
  { id: "risk", label: "Risk Analysis", icon: GaugeCircle },
  { id: "history", label: "History", icon: History },
];

function Progress({ run, stages }) {
  return (
    <Card title="Analysis in progress">
      <p className="mb-4 text-sm text-muted">Each step below reports back as it actually runs. Switch to <b className="text-ink">Live Execution</b> to inspect inputs, tool calls and outputs.</p>
      <ol className="space-y-2">
        {stages.filter((s) => s.group !== "post" && s.id !== "publish_rejected" && s.id !== "impact_retry").map((s) => {
          const st = stageStatus(run, s);
          return (
            <li key={s.id} className={cx("flex items-center gap-3 text-sm", st === "pending" && "text-faint", st === "running" && "text-ink")}>
              <StageIcon status={st} /> <span className="w-44">{s.label}</span>
              <span className="truncate text-xs text-muted">{st === "running" ? "working…" : st === "completed" ? run.stages[s.id]?.message : ""}</span>
              {st === "completed" && <span className="mono ml-auto text-xs text-faint">{duration(run.stages[s.id]?.duration)}</span>}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function Header({ meta, report, pull, runs, onAnalyse, analysing, onCancel, publishSlot }) {
  const pr = { ...(report?.pr || {}), ...(pull || {}), ...(meta?.pr || {}) };
  const title = pull?.title || report?.pr?.title || meta?.pr?.title || meta?.pr?.ref;
  const label = meta?.pr?.number != null ? `#${meta.pr.number}` : meta?.pr?.ref;
  const changed = pull?.changed_files != null ? `${pull.changed_files} files · +${pull.additions ?? 0} −${pull.deletions ?? 0}` : report ? `${report.changes.length} files · ${report.change_units.length} units` : null;
  const ci = pull?.ci;
  const active = meta && (meta.status === "running" || meta.status === "queued");
  return (
    <div className="mb-6 rounded-lg border border-line bg-s1 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><Link to={`/repos/${meta?.repo_id}`} className="hover:text-ink">{meta?.repo_name}</Link><span>·</span><span className="mono">{label}</span>{pull?.draft && <span className="pill bg-s3">draft</span>}</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">{title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted">
            <span>by {pr.author || "unknown"}</span>
            <span className="mono"><GitBranch size={12} className="mr-1 inline" aria-hidden />{pull?.head_ref || meta?.pr?.head_ref || "head"} → {pull?.base_ref || meta?.pr?.base_ref || "base"}</span>
            {pr.head_sha && <span className="inline-flex items-center gap-1 text-faint">head <Id value={short(pr.head_sha)} /></span>}
            {changed && <span>{changed}</span>}
            {ci && <span className="text-faint">CI: {ci.state}{ci.total ? ` (${ci.total} checks)` : ""}</span>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          {meta?.decision ? <DecisionBadge decision={meta.decision} size="lg" /> : <RunStatus status={meta?.status} />}
          <span className="text-xs text-faint">{meta?.status === "completed" ? `Analysed ${ago(meta.ended_at)} (${stamp(meta.ended_at)}) in ${duration(meta.elapsed_s)}` : meta ? `Started ${ago(meta.created_at)}` : ""}</span>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        {runs?.length > 1 && (
          <label className="flex items-center gap-2 text-xs text-muted">Analysis
            <select aria-label="Choose analysis" value={meta?.run_id} onChange={(e) => (window.location.hash = `#/runs/${e.target.value}`)} className="h-8 rounded-md border border-line2 bg-bg px-2 text-sm text-ink">
              {runs.map((m) => <option key={m.run_id} value={m.run_id}>{stamp(m.created_at)} · {m.decision || m.status} · {short(m.pr.head_sha)}</option>)}
            </select></label>
        )}
        <div className="ml-auto flex items-center gap-2">
          {publishSlot}
          {pull?.html_url && <a href={pull.html_url} target="_blank" rel="noreferrer"><Button size="sm" icon={ExternalLink}>View on GitHub</Button></a>}
          {active ? <Button size="sm" variant="danger" icon={Ban} onClick={onCancel}>Cancel</Button>
            : <Button size="sm" variant="primary" icon={RefreshCw} loading={analysing} onClick={onAnalyse}>{analyseLabel(meta)}</Button>}
        </div>
      </div>
    </div>
  );
}

function Workspace({ runId, repoId, pull }) {
  const toast = useToast();
  const { mode, setMode } = useMode();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "summary";
  const view = mode;
  const wanted = params.get("view");
  useEffect(() => { if (wanted === "live" || wanted === "product") setMode(wanted); }, [wanted, setMode]); // ?view= makes either presentation linkable
  const pipeline = useApi("/service/pipeline");
  const { run, meta, report, error, loading, connection, status, active, reload } = useRun(runId);
  const peers = useApi(meta ? `/analyses?repo_id=${encodeURIComponent(meta.repo_id)}&limit=50` : null);
  const { start, busy, dialog } = useAnalyze();
  const repo = useApi(meta ? `/repos/${encodeURIComponent(meta.repo_id)}` : null);
  const live = useApi(meta && !meta.imported ? `/repos/${encodeURIComponent(meta.repo_id)}/pulls/${encodeURIComponent(meta.pr.ref)}` : null);
  const prInfo = pull || live.data;

  const label = meta?.pr?.number != null ? `#${meta.pr.number}` : meta?.pr?.ref;
  useCrumbs(meta ? [{ label: "Repositories", to: "/repos" }, { label: meta.repo_name, to: `/repos/${meta.repo_id}` }, { label: label || "Analysis" }] : []);

  const runs = useMemo(() => (peers.data || []).filter((m) => String(m.pr.ref) === String(meta?.pr?.ref)), [peers.data, meta]);
  const cancel = async () => { try { await api(`/analyses/${runId}/cancel`, { method: "POST" }); toast.push("Cancellation requested.", "info"); } catch (e) { toast.push(e.message, "error"); } };

  if (loading || pipeline.loading) return <SkeletonRows rows={5} />;
  if (error) return <ErrorState error={error} title="Analysis not found" />;
  if (pipeline.error) return <ErrorState error={pipeline.error} onRetry={pipeline.refetch} />;

  const canPublish = repo.data?.kind === "github" && meta?.status === "completed" && Boolean(meta?.pr?.number);
  const again = () => start(repo.data || { id: meta.repo_id, full_name: meta.repo_name }, meta.pr.ref, { llm: meta.options?.llm !== false });
  const setTab = (id) => setParams((p) => { p.set("tab", id); return p; }, { replace: true });
  const stages = pipeline.data.stages;

  return (
    <div>
      <Header meta={meta} report={report} pull={prInfo} runs={runs} onAnalyse={again} analysing={busy != null} onCancel={cancel} publishSlot={canPublish ? <PublishPanel runId={runId} meta={meta} onPublished={() => reload()} /> : null} />
      {status === "failed" && <div className="mb-4"><Banner tone="bad" title="This analysis failed">{meta?.error || run.error}</Banner></div>}
      {status === "cancelled" && <div className="mb-4"><Banner title="This analysis was cancelled">{meta?.error || "It was stopped before it finished."}</Banner></div>}

      {view === "live" ? (
        <LiveView pipeline={pipeline.data} run={run} meta={meta} report={report} connection={connection} status={status} active={active} />
      ) : (
        <div>
          <Tabs tabs={TABS} value={tab} onChange={setTab} label="Pull request sections" />
          <div className="mt-6" role="tabpanel">
            {!report ? (
              active || status === "unknown" ? (tab === "trace" ? <TraceTab run={run} stages={stages} onOpenLive={() => setMode("live")} report={report} meta={meta} /> : <Progress run={run} stages={stages} />)
                : tab === "trace" ? <TraceTab run={run} stages={stages} onOpenLive={() => setMode("live")} report={report} meta={meta} />
                  : tab === "history" ? <HistoryTab runs={runs} currentId={runId} repoId={meta?.repo_id} />
                    : <EmptyState icon={Play} title="No report for this analysis">{status === "failed" ? "The run failed before producing a report." : "The run ended without a report."}</EmptyState>
            ) : tab === "summary" ? <SummaryTab report={report} meta={meta} />
              : tab === "evidence" ? <EvidenceExplorer report={report} />
                : tab === "impact" ? <DiffImpactTab report={report} repoUrl={repo.data?.url} />
                  : tab === "trace" ? <TraceTab run={run} stages={stages} onOpenLive={() => setMode("live")} report={report} meta={meta} />
                    : tab === "risk" ? <RiskTab report={report} />
                      : <HistoryTab runs={runs} currentId={runId} repoId={meta?.repo_id} />}
          </div>
        </div>
      )}
      {dialog}
    </div>
  );
}

/** A pull request's workspace: the chosen analysis (?run=), else its latest, else a prompt to analyse. */
export default function PRWorkspace() {
  const { runId, repoId, ref } = useParams();
  const [search] = useSearchParams();
  const pull = useApi(repoId ? `/repos/${encodeURIComponent(repoId)}/pulls/${encodeURIComponent(ref)}` : null);
  const { start, busy, dialog } = useAnalyze();
  useEffect(() => { window.scrollTo(0, 0); }, [runId, ref]);

  if (runId) return <Workspace key={runId} runId={runId} pull={null} />;
  if (pull.loading) return <SkeletonRows rows={4} />;
  if (pull.error) return <ErrorState error={pull.error} onRetry={pull.refetch} title="Pull request not found" />;
  const p = pull.data;
  const chosen = search.get("run") || p.runs[0]?.run_id;
  if (chosen) return <Workspace key={chosen} runId={chosen} repoId={repoId} pull={p} />;
  return (
    <div>
      <Header meta={{ repo_id: repoId, repo_name: p.repo.full_name, pr: { number: p.number, ref: String(ref) }, status: "none" }} pull={p} report={null} runs={[]} onAnalyse={() => start(p.repo, ref)} analysing={busy != null} />
      <EmptyState icon={Play} title="This pull request has not been analysed"
        action={<Button variant="primary" icon={Play} loading={busy != null} onClick={() => start(p.repo, ref)}>Analyse pull request</Button>}>
        SentinelPR will run the pipeline and show every step as it happens.</EmptyState>
      {dialog}
    </div>
  );
}
