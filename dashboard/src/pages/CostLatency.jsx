import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { NoData, PageHeader, Stat } from "../components.jsx";
import { loadJSON, num, useData } from "../data.js";

const axis = { stroke: "#64748b", fontSize: 11 };

export default function CostLatency() {
  const index = useData("index.json").data;
  const bench = useData(index?.bench?.[0] ? `bench/${index.bench[0]}.json` : null).data;
  const [runs, setRuns] = useState([]);
  useEffect(() => {
    if (index) Promise.all(index.runs.slice(0, 40).map((r) => loadJSON(`runs/${r.run_id}.json`).catch(() => null))).then((x) => setRuns(x.filter(Boolean)));
  }, [index]);

  const eff = bench?.efficiency;
  const nodes = eff ? Object.entries(eff.node_mean_s).map(([node, s]) => ({ node, s })) : [];
  const calls = runs.flatMap((r) => r.llm?.calls_detail || []);
  const byModel = calls.reduce((acc, c) => {
    const key = `${c.provider}:${c.model}`;
    acc[key] = acc[key] || { model: key, calls: 0, tokens: 0, cached: 0 };
    acc[key].calls += 1;
    acc[key].tokens += c.prompt_tokens + c.completion_tokens;
    acc[key].cached += c.cache_hit ? 1 : 0;
    return acc;
  }, {});
  const tiers = calls.reduce((acc, c) => ({ ...acc, [c.tier]: (acc[c.tier] || 0) + 1 }), {});

  return (
    <div>
      <PageHeader title="Cost & Latency" subtitle="Everything runs on free tiers or locally. The router sends easy tasks to small models and only complex ones to the largest free model; every response is cached." />
      {!eff && runs.length === 0 ? <NoData what="runs or benchmark results" /> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Latency p50" value={eff ? `${eff.latency_s.p50}s` : "—"} hint={eff ? `p95 ${eff.latency_s.p95}s per PR` : ""} />
            <Stat label="LLM tokens / PR" value={eff ? num(eff.tokens_per_pr.mean, 0) : "—"} hint="0 when running without providers" />
            <Stat label="LLM calls (recent runs)" value={calls.length} hint={`${calls.filter((c) => c.cache_hit).length} served from cache`} />
            <Stat label="Router tiers" value={Object.entries(tiers).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"} hint="easy · medium · hard" />
          </div>
          {nodes.length > 0 && (
            <div className="card mt-6">
              <div className="card-title">Mean time per pipeline stage (benchmark)</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={nodes} layout="vertical" margin={{ left: 40 }}>
                  <CartesianGrid stroke="#1e293b" /><XAxis type="number" tick={axis} unit="s" /><YAxis type="category" dataKey="node" tick={axis} width={100} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v) => `${num(v, 3)}s`} />
                  <Bar dataKey="s" fill="#38bdf8" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="card mt-6">
            <div className="card-title">Models used</div>
            {Object.keys(byModel).length === 0 ? <p className="text-sm text-slate-500">No LLM calls in the published runs (deterministic mode).</p> : (
              <table className="table">
                <thead><tr><th>model</th><th>calls</th><th>tokens</th><th>cache hits</th></tr></thead>
                <tbody>{Object.values(byModel).map((m) => <tr key={m.model}><td className="font-mono text-xs">{m.model}</td><td>{m.calls}</td><td>{m.tokens}</td><td>{m.cached}</td></tr>)}</tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
