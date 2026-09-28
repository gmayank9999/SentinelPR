import { useEffect, useState } from "react";
import { decisionStyle, statusStyle, useData } from "./data.js";

export function DecisionBadge({ decision, size = "sm" }) {
  const big = size === "lg" ? "px-4 py-1.5 text-lg" : "";
  return <span className={`pill ${decisionStyle[decision] || ""} ${big}`}>{decision || "—"}</span>;
}

export function StatusPill({ status }) {
  return <span className={`pill ${statusStyle[status] || statusStyle.UNCHECKED}`}>{status}</span>;
}

export function Stat({ label, value, hint, tone = "text-slate-100" }) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tone}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="card text-sm text-slate-400">{children}</div>;
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-slate-100">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-400">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

/** Picks a run from data/index.json; remembers the choice across pages. */
export function useRunSelection(initial) {
  const index = useData("index.json");
  const [runId, setRunId] = useState(initial || sessionStorage.getItem("run") || "");
  const runs = index.data?.runs || [];
  useEffect(() => {
    if (!runId && runs.length) setRunId(runs[0].run_id);
  }, [runs, runId]);
  useEffect(() => {
    if (runId) sessionStorage.setItem("run", runId);
  }, [runId]);
  const run = useData(runId ? `runs/${runId}.json` : null);
  return { runs, runId, setRunId, run: run.data, loading: index.loading || run.loading, error: index.error || run.error };
}

export function RunPicker({ runs, runId, setRunId }) {
  if (!runs.length) return null;
  return (
    <select
      value={runId}
      onChange={(e) => setRunId(e.target.value)}
      className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200"
    >
      {runs.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {r.pr?.number ? `#${r.pr.number} ` : ""}
          {r.pr?.title || r.run_id} — {r.decision}
        </option>
      ))}
    </select>
  );
}

export function NoData({ what = "runs", command }) {
  return (
    <Empty>
      No {what} published yet.{" "}
      {command && (
        <>
          Generate them with <code className="rounded bg-slate-800 px-1.5 py-0.5 text-emerald-300">{command}</code>.
        </>
      )}
    </Empty>
  );
}

export function HBar({ value, max = 1, color = "bg-emerald-500", negative = "bg-sky-500" }) {
  const width = Math.min(100, (Math.abs(value) / (max || 1)) * 100);
  return (
    <div className="h-2 w-full rounded bg-slate-800">
      <div className={`h-2 rounded ${value < 0 ? negative : color}`} style={{ width: `${width}%` }} />
    </div>
  );
}
