import { useMemo, useState } from "react";
import { NoData, PageHeader, RunPicker, StatusPill, useRunSelection } from "../components.jsx";
import { api } from "../data.js";

const KIND_STYLE = {
  change: "fill-emerald-500",
  module: "fill-sky-500",
  test: "fill-violet-400",
  history: "fill-amber-400",
  api: "fill-rose-500",
};

/** Evidence graph for a run: changed symbols in the centre, claims around them. */
function runGraph(run) {
  const nodes = new Map();
  const edges = [];
  const add = (id, kind, label, extra = {}) => {
    if (!nodes.has(id)) nodes.set(id, { id, kind, label, ...extra });
    return id;
  };
  (run.change_units || []).filter((u) => u.kind !== "module").forEach((u) => add(u.id, "change", u.qualname, { status: u.change_type }));
  const centre = [...nodes.keys()][0];
  for (const c of run.claims || []) {
    const kind = c.type === "test_impact" ? "test" : c.type === "module_impact" ? "module" : c.type === "api_break" ? "api" : "history";
    const target = kind === "history" ? (c.evidence_ids.find((e) => e.startsWith("issue:")) || c.evidence_ids[0]) : c.target;
    const label = kind === "test" ? target.split("::").pop() : target.split("/").pop();
    add(target, kind, label, { status: c.status, claim: c });
    edges.push({ source: nodes.has(c.target) ? c.target : centre, target, status: c.status });
  }
  return { nodes: [...nodes.values()], edges: edges.filter((e) => e.source && e.source !== e.target) };
}

function layout(nodes) {
  const centre = nodes.filter((n) => n.kind === "change");
  const rest = nodes.filter((n) => n.kind !== "change");
  const pos = {};
  centre.forEach((n, i) => (pos[n.id] = { x: 400 + (i - (centre.length - 1) / 2) * 110, y: 260 }));
  rest.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(rest.length, 1);
    const r = 150 + (i % 2) * 70;
    pos[n.id] = { x: 400 + r * Math.cos(a) * 1.6, y: 260 + r * Math.sin(a) };
  });
  return pos;
}

export default function Evidence() {
  const { runs, runId, setRunId, run } = useRunSelection();
  const [selected, setSelected] = useState(null);
  const [symbol, setSymbol] = useState("");
  const [live, setLive] = useState(null);
  const [error, setError] = useState("");
  const graph = useMemo(() => (run ? runGraph(run) : { nodes: [], edges: [] }), [run]);
  const pos = useMemo(() => layout(graph.nodes), [graph]);

  const explore = async () => {
    setError("");
    try {
      setLive(await api(`/api/graph?symbol=${encodeURIComponent(symbol)}&hops=1`));
    } catch (e) {
      setError(`Live graph needs the API (python -m sentinel.api): ${e.message}`);
    }
  };

  return (
    <div>
      <PageHeader title="Evidence Explorer" subtitle="Changed symbols in the centre; every claim an agent made about them, coloured by what verification concluded.">
        <RunPicker runs={runs} runId={runId} setRunId={setRunId} />
      </PageHeader>
      {!run ? <NoData /> : (
        <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
          <div className="card overflow-hidden">
            <svg viewBox="0 0 800 520" className="w-full">
              {graph.edges.map((e, i) => pos[e.source] && pos[e.target] && (
                <line key={i} x1={pos[e.source].x} y1={pos[e.source].y} x2={pos[e.target].x} y2={pos[e.target].y}
                  strokeWidth="1.2" strokeDasharray={e.status === "REFUTED" ? "4 4" : ""}
                  className={e.status === "VERIFIED" ? "stroke-emerald-600" : e.status === "REFUTED" ? "stroke-rose-700" : "stroke-slate-700"} />
              ))}
              {graph.nodes.map((n) => (
                <g key={n.id} transform={`translate(${pos[n.id].x},${pos[n.id].y})`} className="cursor-pointer" onClick={() => setSelected(n)}>
                  <circle r={n.kind === "change" ? 14 : 8} className={`${KIND_STYLE[n.kind]} ${n.status === "REFUTED" ? "opacity-40" : ""}`} />
                  <text y={n.kind === "change" ? 30 : 20} textAnchor="middle" className="fill-slate-300 text-[10px]">{n.label.slice(0, 26)}</text>
                </g>
              ))}
            </svg>
            <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-400">
              {Object.entries(KIND_STYLE).map(([k, c]) => (
                <span key={k} className="flex items-center gap-1"><svg width="10" height="10"><circle cx="5" cy="5" r="5" className={c} /></svg>{k}</span>
              ))}
              <span>dashed = refuted by execution</span>
            </div>
          </div>
          <div className="space-y-4">
            <div className="card">
              <div className="card-title">Selected</div>
              {!selected ? <p className="text-sm text-slate-500">Click a node.</p> : (
                <div className="space-y-2 text-sm">
                  <div className="font-mono text-xs break-all text-slate-200">{selected.id}</div>
                  {selected.status && <StatusPill status={selected.status} />}
                  {selected.claim && (
                    <>
                      <p className="text-slate-300">{selected.claim.assertion !== "affected" ? selected.claim.assertion : selected.claim.reason}</p>
                      <div className="flex flex-wrap gap-1">{selected.claim.evidence_ids.map((e) => <code key={e} className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px]">{e}</code>)}</div>
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-title">Explore the code graph (live)</div>
              <div className="flex gap-2">
                <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="unierp/exam/grades.py::compute_gpa"
                  className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
                <button onClick={explore} className="rounded-lg bg-emerald-600 px-3 text-sm text-white hover:bg-emerald-500">Go</button>
              </div>
              {error && <p className="mt-2 text-xs text-rose-300">{error}</p>}
              {live && (
                <ul className="mt-3 max-h-80 space-y-1 overflow-auto text-xs">
                  {live.edges.map((e, i) => (
                    <li key={i} className="text-slate-400">
                      <span className="text-slate-200">{e.source.split("::").pop()}</span> <span className="text-emerald-400">{e.type}</span> <span className="text-slate-200">{e.target.split("::").pop()}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
