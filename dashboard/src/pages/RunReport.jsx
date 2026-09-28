import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { DecisionBadge, HBar, NoData, PageHeader, RunPicker, Stat, StatusPill, useRunSelection } from "../components.jsx";
import { num, pct } from "../data.js";

function RiskGauge({ risk, thresholds }) {
  const angle = -90 + (risk ?? 0) * 180;
  const arc = (from, to) => {
    const a = (Math.PI * (180 - from * 180)) / 180, b = (Math.PI * (180 - to * 180)) / 180;
    return `M ${60 + 50 * Math.cos(a)} ${60 - 50 * Math.sin(a)} A 50 50 0 0 1 ${60 + 50 * Math.cos(b)} ${60 - 50 * Math.sin(b)}`;
  };
  const c = thresholds?.canary ?? 0.35, b = thresholds?.block ?? 0.65;
  return (
    <svg viewBox="0 0 120 70" className="w-48">
      <path d={arc(0, c)} className="stroke-emerald-500" strokeWidth="10" fill="none" />
      <path d={arc(c, b)} className="stroke-amber-500" strokeWidth="10" fill="none" />
      <path d={arc(b, 1)} className="stroke-rose-500" strokeWidth="10" fill="none" />
      <line x1="60" y1="60" x2="60" y2="18" className="stroke-slate-100" strokeWidth="2.5" transform={`rotate(${angle} 60 60)`} />
      <circle cx="60" cy="60" r="4" className="fill-slate-100" />
    </svg>
  );
}

export default function RunReport() {
  const { id } = useParams();
  const { runs, runId, setRunId, run } = useRunSelection(id);
  useEffect(() => {
    if (id) setRunId(id);
  }, [id, setRunId]);
  if (!run) return <div><PageHeader title="PR Report" /><NoData /></div>;

  const v = run.verification || {};
  const verdicts = Object.fromEntries((run.verdicts || []).map((x) => [x.claim_id, x]));
  const maxContribution = Math.max(0.01, ...(run.contributions || []).map((c) => Math.abs(c.contribution)));
  const mutants = v.mutation?.mutants || [];

  return (
    <div>
      <PageHeader title={run.pr?.title || "PR Report"} subtitle={`${run.pr?.number ? `#${run.pr.number} · ` : ""}${run.pr?.author || ""} · ${run.run_id}`}>
        <RunPicker runs={runs} runId={runId} setRunId={setRunId} />
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card flex items-center gap-6">
          <RiskGauge risk={run.risk} thresholds={run.thresholds} />
          <div>
            <DecisionBadge decision={run.decision} size="lg" />
            <div className="mt-2 text-3xl font-semibold">{run.risk == null ? "—" : num(run.risk)}</div>
            <div className="text-xs text-slate-500">{run.calibrated ? "calibrated risk" : "prior model"} · {run.risk_model}</div>
          </div>
        </div>
        <div className="card lg:col-span-2">
          <div className="card-title">Decision</div>
          <p className="text-sm text-slate-300">{run.decision_reason}</p>
          <p className="mt-3 text-sm text-slate-400">{run.explanation}</p>
          {run.history?.memories?.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-sm text-amber-200/80">
              {run.history.memories.map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Tests" value={`${v.tests?.passed ?? 0} / ${v.tests?.selected ?? 0}`} hint={`${v.tests?.failed?.length ?? 0} failing · ${pct(run.selection?.time_saved_ratio)} suite time saved`} />
        <Stat label="Changed lines executed" value={pct(v.changed_lines?.coverage)} hint={`${v.changed_lines?.covered ?? 0} of ${v.changed_lines?.total ?? 0} lines`} />
        <Stat label="Mutation score" value={pct(v.mutation?.score)} hint={`${mutants.filter((m) => m.status === "survived").length} of ${mutants.length} mutants survived`} tone={v.mutation?.score != null && v.mutation.score < 0.6 ? "text-amber-300" : "text-slate-100"} />
        <Stat label="Claims" value={(run.claims || []).length} hint={Object.entries(run.verdict_counts || {}).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(" · ")} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <div className="card-title">Why this score</div>
          <div className="space-y-2">
            {(run.contributions || []).map((c) => (
              <div key={c.feature} className="grid grid-cols-[1fr_auto] items-center gap-x-3 text-sm">
                <div className="truncate text-slate-300" title={c.feature}>{c.label} <span className="text-slate-500">({num(c.value)})</span></div>
                <div className={`font-mono ${c.contribution > 0 ? "text-rose-300" : "text-sky-300"}`}>{c.contribution > 0 ? "+" : ""}{num(c.contribution)}</div>
                <div className="col-span-2"><HBar value={c.contribution} max={maxContribution} color="bg-rose-500" /></div>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <div className="card-title">Mutants on changed lines</div>
          {mutants.length === 0 ? <p className="text-sm text-slate-500">No executable changed lines to mutate.</p> : (
            <table className="table">
              <thead><tr><th>line</th><th>mutation</th><th>result</th></tr></thead>
              <tbody>
                {mutants.map((m) => (
                  <tr key={m.id}>
                    <td className="font-mono text-xs">{m.file.split("/").pop()}:{m.line}</td>
                    <td className="font-mono text-xs"><span className="text-slate-400">{m.original}</span> → <span className="text-slate-200">{m.mutated}</span></td>
                    <td><span className={`pill ${m.status === "survived" ? "bg-amber-500/15 text-amber-300" : "bg-emerald-500/15 text-emerald-300"}`}>{m.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card mt-4">
        <div className="card-title">Claims and verdicts</div>
        <table className="table">
          <thead><tr><th>id</th><th>agent</th><th>type</th><th>target</th><th>status</th><th>evidence</th></tr></thead>
          <tbody>
            {(run.claims || []).map((c) => (
              <tr key={c.claim_id}>
                <td className="font-mono text-xs">{c.claim_id}</td>
                <td>{c.agent}</td>
                <td className="text-slate-400">{c.type}</td>
                <td className="max-w-md">
                  <div className="font-mono text-xs text-slate-200">{c.target}</div>
                  <div className="text-xs text-slate-500">{c.assertion !== "affected" ? c.assertion : c.reason}</div>
                </td>
                <td><StatusPill status={c.status} /></td>
                <td className="text-xs text-slate-400">{verdicts[c.claim_id]?.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {v.changed_lines?.uncovered?.length > 0 && (
        <div className="card mt-4">
          <div className="card-title">Changed lines no test executes</div>
          <div className="flex flex-wrap gap-2">
            {v.changed_lines.uncovered.map((u) => <code key={u} className="rounded bg-slate-800 px-2 py-1 text-xs text-amber-200">{u}</code>)}
          </div>
        </div>
      )}
    </div>
  );
}
