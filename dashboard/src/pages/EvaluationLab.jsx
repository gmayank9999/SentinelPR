import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { NoData, PageHeader, Stat } from "../components.jsx";
import { num, pct, useData } from "../data.js";

const COLORS = ["#10b981", "#38bdf8", "#f59e0b", "#f43f5e", "#a78bfa", "#94a3b8", "#fb923c", "#22d3ee", "#e879f9"];
const HEADLINE = ["SentinelPR", "B-JIT", "B-tests", "B-LLM", "SentinelPR (prior, untrained)", "SentinelPR (LightGBM)"];
const axis = { stroke: "#64748b", fontSize: 11 };

function CurveChart({ curves, kind, x, y }) {
  const names = HEADLINE.filter((n) => curves[n]?.[kind]);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart margin={{ top: 5, right: 10, bottom: 30, left: 0 }}>
        <CartesianGrid stroke="#1e293b" />
        <XAxis type="number" dataKey="x" domain={[0, 1]} tick={axis} label={{ value: x, position: "insideBottom", offset: -8, fill: "#64748b", fontSize: 11 }} />
        <YAxis type="number" dataKey="y" domain={[0, 1]} tick={axis} label={{ value: y, angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 11 }} />
        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v) => num(v)} />
        <Legend verticalAlign="top" wrapperStyle={{ fontSize: 11, paddingBottom: 8 }} />
        {names.map((n, i) => (
          <Line key={n} name={n} data={curves[n][kind].map(([a, b]) => ({ x: a, y: b }))} dataKey="y" type="stepAfter" dot={false} stroke={COLORS[i]} strokeWidth={n === "SentinelPR" ? 2.5 : 1.5} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function Reliability({ rows }) {
  const data = (rows || []).map((r) => ({ x: r.mean_pred, y: r.frac_pos, z: r.count }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ScatterChart margin={{ top: 5, right: 10, bottom: 30, left: 0 }}>
        <CartesianGrid stroke="#1e293b" />
        <XAxis type="number" dataKey="x" domain={[0, 1]} tick={axis} name="predicted" label={{ value: "predicted risk", position: "insideBottom", offset: -8, fill: "#64748b", fontSize: 11 }} />
        <YAxis type="number" dataKey="y" domain={[0, 1]} tick={axis} name="observed" label={{ value: "defective share", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 11 }} />
        <ZAxis dataKey="z" range={[40, 400]} name="PRs" />
        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v) => num(v)} />
        <Scatter data={[{ x: 0, y: 0, z: 1 }, { x: 1, y: 1, z: 1 }]} line={{ stroke: "#475569", strokeDasharray: "4 4" }} fill="transparent" shape={() => null} />
        <Scatter data={data} fill="#10b981" />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export default function EvaluationLab() {
  const index = useData("index.json").data;
  const [repo, setRepo] = useState(null);
  const current = repo || index?.bench?.[0];
  const bench = useData(current ? `bench/${current}.json` : null).data;
  const qa = useData(index?.qa ? "qa.json" : null).data;
  if (!bench) return <div><PageHeader title="Evaluation Lab" /><NoData what="benchmark results" command="python -m bench.run_matrix && python -m bench.analysis.report" /></div>;

  const { dataset, gate, impact, claims } = bench;
  const table = gate.table;
  const categories = Object.entries(gate.per_category).map(([cat, v]) => ({ cat, PASS: v.PASS, CANARY: v.CANARY, BLOCK: v.BLOCK, defective: v.defective }));
  const impactRows = ["lexical", "static", "sentinel", "sentinel_verified"].filter((m) => impact[m]?.tests).map((m) => ({ method: m, ...impact[m].tests }));

  return (
    <div>
      <PageHeader title="Evaluation Lab" subtitle={`${dataset.prs} generated PRs on ${dataset.bases.length} base revisions, labelled by execution (base tests + hidden oracle). Gate metrics are forward-chained: no PR is scored by a model that saw a later revision.`}>
        {index?.bench?.length > 1 && (
          <select value={current} onChange={(e) => setRepo(e.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            {index.bench.map((b) => <option key={b}>{b}</option>)}
          </select>
        )}
      </PageHeader>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="PR-AUC" value={num(table.SentinelPR?.pr_auc)} hint={`vs JIT ${num(table["B-JIT"]?.pr_auc)} · tests-pass ${num(table["B-tests"]?.pr_auc)}`} tone="text-emerald-300" />
        <Stat label="Recall @ 5% FPR" value={pct(table.SentinelPR?.recall_at_5fpr)} hint={`JIT ${pct(table["B-JIT"]?.recall_at_5fpr)}`} />
        <Stat label="Calibration (ECE)" value={num(table.SentinelPR?.ece, 3)} hint={`Brier ${num(table.SentinelPR?.brier, 3)}`} />
        <Stat label="Claim precision" value={`${pct(claims.precision_before_verification)} → ${pct(claims.precision_after_verification)}`} hint="impact claims, before → after verification" />
      </div>

      <div className="card mt-6 overflow-x-auto">
        <div className="card-title">RQ3 · Release-risk gate</div>
        <table className="table">
          <thead><tr><th>method</th><th>ROC-AUC</th><th>PR-AUC</th><th>95% CI</th><th>recall@5%FPR</th><th>Brier</th><th>ECE</th><th>false-block</th><th>block recall</th><th>McNemar p</th></tr></thead>
          <tbody>
            {Object.entries(table).map(([name, m]) => (
              <tr key={name} className={name === "SentinelPR" ? "text-emerald-200" : ""}>
                <td>{name}</td><td>{num(m.roc_auc)}</td><td>{num(m.pr_auc)}</td>
                <td className="text-slate-500">{m.pr_auc_ci ? `${num(m.pr_auc_ci[0])}–${num(m.pr_auc_ci[1])}` : "—"}</td>
                <td>{pct(m.recall_at_5fpr)}</td><td>{num(m.brier, 3)}</td><td>{num(m.ece, 3)}</td><td>{pct(m.false_block_rate)}</td><td>{pct(m.block_recall)}</td>
                <td className="text-slate-400">{gate.mcnemar_vs_sentinel[name] ? num(gate.mcnemar_vs_sentinel[name].p_value, 3) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-3">
        <div className="card"><div className="card-title">ROC</div><CurveChart curves={gate.curves} kind="roc" x="false positive rate" y="true positive rate" /></div>
        <div className="card"><div className="card-title">Precision–recall</div><CurveChart curves={gate.curves} kind="pr" x="recall" y="precision" /></div>
        <div className="card"><div className="card-title">Reliability (SentinelPR)</div><Reliability rows={gate.curves.SentinelPR?.reliability} /></div>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <div className="card-title">Decisions by category</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={categories}>
              <CartesianGrid stroke="#1e293b" /><XAxis dataKey="cat" tick={axis} /><YAxis tick={axis} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="PASS" stackId="d" fill="#10b981" /><Bar dataKey="CANARY" stackId="d" fill="#f59e0b" /><Bar dataKey="BLOCK" stackId="d" fill="#f43f5e" />
            </BarChart>
          </ResponsiveContainer>
          <p className="mt-2 text-xs text-slate-500">C1 refactor · C2 fault caught by tests · HF fault only hidden tests catch · C3 API ripple · C4 reverted bug fix · C5 config · C6 tests/docs · C7 adversarial</p>
        </div>
        <div className="card">
          <div className="card-title">RQ1 · Change impact (tests)</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={impactRows}>
              <CartesianGrid stroke="#1e293b" /><XAxis dataKey="method" tick={axis} /><YAxis domain={[0, 1]} tick={axis} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v) => num(v)} /><Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="macro_precision" name="precision" fill="#38bdf8" /><Bar dataKey="macro_recall" name="recall" fill="#a78bfa" /><Bar dataKey="macro_f1" name="F1" fill="#10b981" />
            </BarChart>
          </ResponsiveContainer>
          <p className="mt-2 text-xs text-slate-400">
            Test selection: {num(impact.selection.mean_selected, 1)} tests per PR, {pct(impact.selection.mean_time_saved)} of suite time saved, safe-selection rate {pct(impact.selection.safe_selection_rate)}.
          </p>
        </div>
      </div>

      {qa && (
        <div className="card mt-6">
          <div className="card-title">RQ4 · Repository Q&A ({qa.engine})</div>
          <table className="table">
            <thead><tr><th>condition</th><th>accuracy</th><th>citation precision</th><th>hallucination</th>{Object.keys(Object.values(qa.conditions)[0].by_category).map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {Object.entries(qa.conditions).map(([mode, r]) => (
                <tr key={mode}>
                  <td>{mode}</td><td>{pct(r.overall.accuracy)}</td><td>{pct(r.overall.citation_precision)}</td><td>{pct(r.overall.hallucination_rate)}</td>
                  {Object.values(r.by_category).map((c, i) => <td key={i} className="text-slate-400">{pct(c.accuracy)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
