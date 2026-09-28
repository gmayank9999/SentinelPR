import { useEffect, useMemo, useState } from "react";
import { NoData, PageHeader, RunPicker, useRunSelection } from "../components.jsx";

// Layout of the LangGraph state machine (columns left to right).
const LAYOUT = [
  ["guard_in"],
  ["parse_diff"],
  ["impact", "historian", "signals"],
  ["select_tests"],
  ["verify"],
  ["reconcile", "impact_retry"],
  ["score"],
  ["decide"],
  ["guard_out", "publish_rejected"],
];
const EDGES = [
  ["guard_in", "parse_diff"], ["guard_in", "publish_rejected"],
  ["parse_diff", "impact"], ["parse_diff", "historian"], ["parse_diff", "signals"],
  ["impact", "select_tests"], ["historian", "select_tests"], ["signals", "select_tests"],
  ["select_tests", "verify"], ["verify", "reconcile"], ["reconcile", "impact_retry"], ["reconcile", "score"],
  ["impact_retry", "score"], ["score", "decide"], ["decide", "guard_out"],
];
const W = 150, H = 54, GX = 175, GY = 88;

function positions() {
  const pos = {};
  LAYOUT.forEach((column, x) => {
    column.forEach((node, y) => {
      const offset = (3 - column.length) * GY / 2;
      pos[node] = { x: 20 + x * GX, y: 30 + offset + y * GY };
    });
  });
  return pos;
}

export default function Pipeline() {
  const { runs, runId, setRunId, run } = useRunSelection();
  const [clock, setClock] = useState(0);
  const [playing, setPlaying] = useState(true);
  const trace = run?.trace || [];
  const t0 = trace.length ? Math.min(...trace.map((t) => t.start)) : 0;
  const total = trace.length ? Math.max(...trace.map((t) => t.end)) - t0 : 0;
  const pos = useMemo(positions, []);
  const byNode = Object.fromEntries(trace.map((t) => [t.node, t]));

  useEffect(() => setClock(0), [runId]);
  useEffect(() => {
    if (!playing || !total) return;
    // replay the real timings, compressed so the whole run animates in ~6 seconds
    const speed = Math.max(total / 6, 0.05);
    const id = setInterval(() => setClock((c) => (c >= total ? total : c + speed * 0.05)), 50);
    return () => clearInterval(id);
  }, [playing, total]);

  const state = (node) => {
    const t = byNode[node];
    if (!t) return "idle";
    if (clock < t.start - t0) return "pending";
    if (clock < t.end - t0) return "active";
    return t.status === "error" ? "error" : "done";
  };
  // [box, label] classes; stroke stays on the box so labels are not outlined
  const style = {
    idle: ["fill-slate-900 stroke-slate-800", "fill-slate-600"],
    pending: ["fill-slate-900 stroke-slate-600", "fill-slate-400"],
    active: ["fill-emerald-950 stroke-emerald-400", "fill-emerald-200"],
    done: ["fill-slate-800 stroke-emerald-600", "fill-slate-100"],
    error: ["fill-rose-950 stroke-rose-500", "fill-rose-200"],
  };

  return (
    <div>
      <PageHeader title="Live Pipeline" subtitle="The LangGraph trace of a run, replayed with its real timings. Impact, Historian and Signals run in parallel.">
        <div className="flex gap-2">
          <RunPicker runs={runs} runId={runId} setRunId={setRunId} />
          <button className="rounded-lg bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700" onClick={() => { setClock(0); setPlaying(true); }}>Replay</button>
        </div>
      </PageHeader>
      {!run ? <NoData /> : (
        <>
          <div className="card overflow-x-auto">
            <svg viewBox={`0 0 ${LAYOUT.length * GX + 20} ${3 * GY + 40}`} className="w-full min-w-[900px]">
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L10,5 L0,10 z" className="fill-slate-600" />
                </marker>
              </defs>
              {EDGES.map(([a, b]) => (
                <line key={a + b} x1={pos[a].x + W} y1={pos[a].y + H / 2} x2={pos[b].x} y2={pos[b].y + H / 2}
                  className={byNode[a] && byNode[b] ? "stroke-slate-500" : "stroke-slate-800"} strokeWidth="1.5" markerEnd="url(#arrow)" />
              ))}
              {Object.entries(pos).map(([node, p]) => {
                const s = state(node);
                const t = byNode[node];
                return (
                  <g key={node} transform={`translate(${p.x},${p.y})`}>
                    <rect width={W} height={H} rx="10" strokeWidth="1.5" className={`${style[s][0]} ${s === "active" ? "node-active" : ""}`} />
                    <text x={W / 2} y="22" textAnchor="middle" className={`${style[s][1]} text-[13px] font-medium`}>{node}</text>
                    <text x={W / 2} y="40" textAnchor="middle" className="fill-slate-400 text-[11px]">
                      {t ? `${(t.end - t.start).toFixed(2)}s${t.tokens ? ` · ${t.tokens} tok` : ""}` : "not run"}
                    </text>
                  </g>
                );
              })}
            </svg>
            <div className="mt-2 h-1.5 rounded bg-slate-800">
              <div className="h-1.5 rounded bg-emerald-500 transition-all" style={{ width: `${total ? (clock / total) * 100 : 0}%` }} />
            </div>
          </div>
          <div className="card mt-6">
            <div className="card-title">Trace</div>
            <table className="table">
              <thead><tr><th>node</th><th>start</th><th>duration</th><th>tokens</th><th>model</th><th>retries</th><th>note</th></tr></thead>
              <tbody>
                {trace.map((t, i) => (
                  <tr key={i}>
                    <td className="font-mono">{t.node}</td>
                    <td>{(t.start - t0).toFixed(2)}s</td>
                    <td>{(t.end - t.start).toFixed(2)}s</td>
                    <td>{t.tokens}</td>
                    <td className="text-slate-400">{t.model || "—"}</td>
                    <td>{t.retries}</td>
                    <td className="text-slate-400">{t.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
