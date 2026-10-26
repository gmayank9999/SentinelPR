import { useState } from "react";
import { ArrowLeft, Cpu } from "lucide-react";
import { clock, duration, num, pct } from "../../lib/format.js";
import { verifySteps } from "../../lib/runModel.js";
import { Banner, Json, KV, RiskMeter, STAGE, StageIcon, Tabs, cx } from "../../ui.jsx";
import { annotateContributions } from "../run/derive.js";
import { ClaimsTable, ContributionBars, Disclosure, FeatureTable, MutantsTable, Tiles, TestsList } from "../run/parts.jsx";

function Outputs({ id, detail, info, report }) {
  const d = detail || {};
  switch (id) {
    case "intake":
      return <KV rows={[["Base", report?.pr?.base_sha], ["Head", report?.pr?.head_sha], ["Author", report?.pr?.author], ["Title", report?.pr?.title]]} />;
    case "index":
      return <p className="text-sm text-muted">{info?.cached ? "The index for this base revision was already built and was reused (cached by SHA)." : "The base revision was indexed: commit history, SZZ links, the per-test coverage map and the retrieval index."}</p>;
    case "guard_in":
      return (
        <div className="space-y-3">
          {d.rejected && <Banner tone="bad" title="PR rejected">{d.reason}</Banner>}
          <Tiles items={[["Secrets found", d.secrets_found ?? 0, "", d.secrets_found ? "text-bad" : ""], ["Injection attempts", d.injection_attempts ?? 0, "", d.injection_attempts ? "text-warn" : ""]]} />
          {(d.events || []).length === 0 ? <p className="text-sm text-muted">No guard events: size limits, secrets and injection scans were clean.</p> : (
            <ul className="space-y-2">{d.events.map((e, i) => <li key={i} className="rounded border border-line p-2 text-sm"><span className="pill bg-s3 text-muted">{e.guard}</span> <span className={e.severity === "block" ? "text-bad" : "text-warn"}>{e.severity}</span><div className="mt-1 text-muted">{e.message}{e.location ? ` — ${e.location}` : ""}</div></li>)}</ul>
          )}
        </div>
      );
    case "parse_diff":
      return (
        <div className="space-y-3">
          <Tiles items={[["Files", d.files?.length ?? "—"], ["Change units", d.units?.length ?? "—"], ["Diff lines", d.diff_lines ?? "—"]]} />
          <table className="table"><thead><tr><th>Changed unit</th><th>Kind</th><th>Change</th></tr></thead><tbody>
            {(d.units || []).map((u) => <tr key={u.id}><td className="mono break-all">{u.id}</td><td>{u.kind}</td><td>{u.change_type}{u.signature_changed ? " · signature changed" : ""}</td></tr>)}</tbody></table>
        </div>
      );
    case "impact":
    case "historian":
    case "impact_retry": {
      const claims = d.claims || d.revised_claims || [];
      return (
        <div className="space-y-3">
          <KV rows={[d.mode && ["Mode", d.mode], d.llm_risk != null && ["LLM risk rating", `${d.llm_risk} — one input among many; it cannot set the decision`], d.discarded_claims?.length && ["Discarded claims", `${d.discarded_claims.length} cited evidence the agent was not given`]]} />
          <ClaimsTable claims={claims.map((c) => ({ ...c, agent: c.agent || id }))} />
          {d.memories?.length > 0 && <Disclosure title="History memories" meta={d.memories.length}><ul className="space-y-1 text-sm text-muted">{d.memories.map((m, i) => <li key={i}>{m}</li>)}</ul></Disclosure>}
        </div>
      );
    }
    case "signals":
      return <div className="space-y-3"><KV rows={[["Change intent", `${d.intent ?? "—"} (${d.intent_source ?? "—"})`]]} /><Disclosure title="Signals" meta={Object.keys(d.features || {}).length}><FeatureTable features={d.features} /></Disclosure></div>;
    case "select_tests":
      return (
        <div className="space-y-3">
          <Tiles items={[["Selected", `${d.selected_count ?? 0} of ${d.total_available ?? "?"}`], ["Suite time saved", pct(d.time_saved_ratio)], ["Estimated", duration(d.estimated_s)], ["Full suite", duration(d.full_suite_s)]]} />
          <TestsList tests={d.selected || []} reasons={d.reasons} />
        </div>
      );
    case "verify": {
      const steps = verifySteps(info);
      const v = report?.verification;
      const mutants = steps.mutants.length ? steps.mutants : v?.mutation?.mutants || [];
      return (
        <div className="space-y-3">
          <Tiles items={[
            ["Tests passed", d.tests?.passed ?? "—", `${d.tests?.selected ?? "?"} selected`], ["Tests failed", d.tests?.failed?.length ?? "—", d.tests?.failed?.[0]?.split("::").pop(), d.tests?.failed?.length ? "text-bad" : ""],
            ["Changed lines run", d.changed_lines ? `${d.changed_lines.covered}/${d.changed_lines.total}` : "—", d.changed_lines?.uncovered?.length ? `${d.changed_lines.uncovered.length} never executed` : ""],
            ["Mutation score", d.mutation?.score != null ? pct(d.mutation.score) : "Not measured", d.mutation ? `${d.mutation.survived} survived` : ""],
          ]} />
          {d.tests?.collection_error && <Banner tone="bad" title="Tests could not be collected">{d.tests.collection_error}</Banner>}
          <div><h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">Mutants on changed lines</h4><MutantsTable mutants={mutants} /></div>
          {d.verdicts && <KV rows={[["Claims verified", d.verdicts.VERIFIED], ["Claims refuted", d.verdicts.REFUTED], ["Unverifiable", d.verdicts.UNVERIFIABLE]]} />}
        </div>
      );
    }
    case "reconcile":
      return <div className="space-y-3"><Tiles items={Object.entries(d.counts || {}).map(([k, n]) => [k.toLowerCase(), n])} /><KV rows={[["Impact claims refuted", pct(d.impact_refuted_ratio)], ["Retry the Impact agent", d.should_retry_impact ? "Yes (over half refuted)" : "No"]]} /></div>;
    case "score":
      return (
        <div className="space-y-4">
          <KV rows={[["Calibrated risk", d.risk != null ? num(d.risk, 3) : "Not measured"], ["Model", d.model], ["Calibrated", d.calibrated ? "Yes (Platt scaling)" : "No"]]} />
          <div><h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Feature contributions (log-odds, before calibration)</h4><ContributionBars contributions={report ? annotateContributions(d.contributions || report.contributions, report) : d.contributions} /></div>
          <Disclosure title="All feature values" meta={Object.keys(d.features || {}).length}><FeatureTable features={d.features} contributions={d.contributions} /></Disclosure>
        </div>
      );
    case "decide":
      return (
        <div className="space-y-3">
          <KV rows={[["Decision", <b key="d">{d.decision}</b>], ["Why", d.reason], ["Explanation by", d.explanation_source === "llm" ? "LLM (every sentence must cite evidence)" : "deterministic template"]]} />
          {report && <RiskMeter risk={report.risk} thresholds={d.thresholds} />}
          <p className="text-xs text-faint">Rule: verified test failures or risk ≥ block threshold → BLOCK; risk ≥ canary threshold → CANARY; otherwise PASS. LLM text cannot change this.</p>
        </div>
      );
    case "guard_out":
      return <p className="text-sm text-muted">The decision was recomputed from the risk model and policy and compared with the published one. Decision after the integrity check: <b className="text-ink">{d.decision_after_integrity_check ?? "—"}</b>.</p>;
    default:
      return Object.keys(d).length ? <Json value={d} label="Recorded output" /> : <p className="text-sm text-muted">No output was recorded for this stage.</p>;
  }
}

function ToolCalls({ info }) {
  const steps = info?.substeps?.filter((e) => e.data?.step !== "mutant") || [];
  const calls = info?.llmCalls || [];
  if (!steps.length && !calls.length) return <p className="text-sm text-muted">No tool or model calls were recorded for this stage.</p>;
  return (
    <div className="space-y-4">
      {steps.length > 0 && <div><h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">Executions</h4><ul className="space-y-2">{steps.map((e) => (
        <li key={e.seq} className="rounded border border-line p-2.5 text-sm"><div className="flex justify-between gap-3"><span className="font-medium">{e.data.step}</span><span className="mono text-faint">{clock(e.ts)}</span></div>
          <div className="text-muted">{e.message}</div>{e.data.command && <div className="mono mt-1 text-faint">{e.data.command}</div>}</li>))}</ul></div>}
      {calls.length > 0 && <div><h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">Model calls</h4><table className="table"><thead><tr><th>Task</th><th>Model</th><th className="text-right">Tokens</th><th className="text-right">Latency</th></tr></thead><tbody>
        {calls.map((e) => <tr key={e.seq}><td>{e.data.task}{e.data.cache_hit ? " (cached)" : ""}{e.data.error ? <span className="text-warn"> {e.data.error}</span> : ""}</td><td className="mono">{e.data.provider}:{e.data.model}<div className="text-faint">tier {e.data.tier}</div></td>
          <td className="mono text-right">{e.data.prompt_tokens + e.data.completion_tokens}</td><td className="mono text-right">{duration(e.data.latency_s)}</td></tr>)}</tbody></table>
        <p className="mt-1.5 text-xs text-faint">Prompts and completions are not stored in events; the prompt versions are recorded on the run.</p></div>}
    </div>
  );
}

/** Everything known about one pipeline stage of one run. */
export default function Inspector({ stage, def, info, status, upstream, report, onSelect }) {
  const [tab, setTab] = useState("output");
  if (!def) return <div className="p-6 text-sm text-muted">Select a stage in the graph to inspect what it received, what it did and what it produced.</div>;
  const st = STAGE[status];
  const tabs = [
    { id: "overview", label: "Overview" }, { id: "inputs", label: "Inputs" }, { id: "output", label: "Output" },
    { id: "calls", label: "Tool calls", count: (info?.substeps?.filter((e) => e.data?.step !== "mutant").length || 0) + (info?.llmCalls?.length || 0) || undefined },
    { id: "logs", label: "Events", count: info?.eventSeqs?.length },
  ];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center gap-2"><StageIcon status={status} /><h3 className="text-sm font-semibold">{def.label}</h3><span className={cx("text-xs", st.tone)}>{st.label}</span></div>
        <p className="mt-1 text-xs text-muted">{def.about}</p>
      </div>
      <div className="px-2"><Tabs label="Stage details" tabs={tabs} value={tab} onChange={setTab} /></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {info?.reconstructed && <div className="mb-3"><Banner title="Reconstructed from a recorded trace">Order and timings are real; sub-steps (individual mutants, test commands) were not recorded for runs made before live events existed.</Banner></div>}
        {tab === "overview" && (
          <div className="space-y-3">
            <KV rows={[
              ["Status", st.label], ["Started", info?.startedAt ? clock(info.startedAt) : "—"], ["Finished", info?.endedAt ? clock(info.endedAt) : "—"],
              ["Duration", status === "completed" || status === "failed" ? duration(info?.duration) : "—"],
              def.group === "pipeline" && ["Tokens", info?.tokens ? info.tokens : status === "completed" ? "0 (no model call)" : "—"],
              info?.model && ["Model", <span key="m" className="mono"><Cpu size={12} className="mr-1 inline" aria-hidden />{info.model}</span>],
              info?.retries > 0 && ["Retries", info.retries], info?.message && ["Result", info.message],
            ]} />
            {status === "skipped" && <Banner title="Skipped">{info?.reason || "Not reached in this run."}</Banner>}
            {status === "failed" && <Banner tone="bad" title="Stage failed">{info?.error}</Banner>}
            {status === "pending" && <p className="text-sm text-muted">This stage has not started. If the run has finished, no telemetry exists for it.</p>}
          </div>
        )}
        {tab === "inputs" && (
          <div className="space-y-3">
            <p className="text-sm text-muted">This stage consumes the outputs of:</p>
            {upstream.length === 0 ? <p className="text-sm text-faint">Nothing upstream; this is where the run begins.</p> : (
              <ul className="space-y-1.5">{upstream.map((u) => (
                <li key={u.id}><button type="button" onClick={() => onSelect(u.id)} className="flex w-full items-center gap-2 rounded-md border border-line px-3 py-2 text-left text-sm hover:bg-s2"><ArrowLeft size={13} aria-hidden /> {u.label}<span className="ml-auto text-xs text-faint">{STAGE[u.status].label}</span></button></li>))}</ul>
            )}
            {def.id === "verify" && info?.substeps?.[0]?.data?.tests != null && <KV rows={[["Tests sent to pytest", info.substeps[0].data.tests], ["Coverage package", info.substeps[0].data.coverage_package], ["Timeout", `${info.substeps[0].data.timeout_s}s`]]} />}
          </div>
        )}
        {tab === "output" && (status === "pending" || status === "waiting" ? <p className="text-sm text-muted">No output yet.</p>
          : status === "skipped" ? <p className="text-sm text-muted">{info?.reason}</p> : <Outputs id={stage} detail={info?.detail} info={info} report={report} />)}
        {tab === "calls" && <ToolCalls info={info} />}
        {tab === "logs" && (info?.eventSeqs?.length ? <p className="text-sm text-muted">{info.eventSeqs.length} event(s) belong to this stage: sequence {info.eventSeqs.join(", ")}. See the timeline for their messages.</p> : <p className="text-sm text-muted">No events for this stage.</p>)}
      </div>
    </div>
  );
}

