import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { NoData, PageHeader, Stat } from "../components.jsx";
import { pct, useData } from "../data.js";

const axis = { stroke: "#64748b", fontSize: 11 };

export default function CanaryMonitor() {
  const index = useData("index.json").data;
  const files = index?.canary || [];
  const [file, setFile] = useState(null);
  const current = file || files[0];
  const report = useData(current ? `canary/${current}` : null).data;

  if (!report) return <div><PageHeader title="Canary Monitor" /><NoData what="canary rollouts" command="python -m sentinel.canary.run --canary-env UNIERP_FAULT=slow" /></div>;

  const steps = report.steps.map((s) => ({
    step: `${Math.round(s.weight * 100)}%`,
    "stable p95": s.stable.p95_ms, "canary p95": s.canary.p95_ms,
    "stable errors": +(s.stable.error_rate * 100).toFixed(2), "canary errors": +(s.canary.error_rate * 100).toFixed(2),
    healthy: s.healthy, breaches: s.breaches,
  }));
  const rolledBack = report.outcome === "rolled_back";

  return (
    <div>
      <PageHeader title="Canary Monitor" subtitle="Traffic shifts 10% → 25% → 50% → 100% while the SLO monitor compares canary against stable on each step's own traffic; the first breach sends everything back.">
        <select value={current} onChange={(e) => setFile(e.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
          {files.map((f) => <option key={f}>{f}</option>)}
        </select>
      </PageHeader>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Outcome" value={rolledBack ? "Rolled back" : "Promoted"} tone={rolledBack ? "text-rose-300" : "text-emerald-300"} hint={report.labels?.scenario ? `scenario: ${report.labels.scenario}` : ""} />
        <Stat label="Time to detection" value={report.time_to_detection_s != null ? `${report.time_to_detection_s}s` : "—"} hint="from rollout start" />
        <Stat label="Traffic exposed" value={pct(report.exposed_share, 1)} hint={`${report.canary_requests} of ${report.total_requests} requests hit the canary`} />
        <Stat label="Steps completed" value={`${report.steps.length} / 4`} hint={`SLO: errors ≤ ${pct(report.slo.max_error_rate)} · p95 ≤ ${report.slo.p95_ratio}× stable`} />
      </div>
      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <div className="card-title">p95 latency per step (ms)</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={steps}><CartesianGrid stroke="#1e293b" /><XAxis dataKey="step" tick={axis} /><YAxis tick={axis} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="stable p95" fill="#38bdf8" /><Bar dataKey="canary p95" fill="#f59e0b" /></BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-title">Error rate per step (%)</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={steps}><CartesianGrid stroke="#1e293b" /><XAxis dataKey="step" tick={axis} /><YAxis tick={axis} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="stable errors" fill="#38bdf8" /><Bar dataKey="canary errors" fill="#f43f5e" /></BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="card mt-6">
        <div className="card-title">Timeline</div>
        <ol className="space-y-2 text-sm">
          {report.events.map((e, i) => (
            <li key={i} className="flex gap-3">
              <span className="w-16 shrink-0 font-mono text-slate-500">+{(e.t - report.started).toFixed(1)}s</span>
              <span className={e.type === "rollback" ? "text-rose-300" : e.type === "promote" ? "text-emerald-300" : "text-slate-300"}>
                {e.type === "shift" ? `traffic → ${Math.round(e.weight * 100)}% canary` : e.type === "rollback" ? `rollback: ${e.reason.join("; ")}` : "promoted to 100%"}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
