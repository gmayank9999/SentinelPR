import { useState } from "react";
import { NoData, PageHeader, Stat } from "../components.jsx";
import { num, pct, useData } from "../data.js";

export default function EvaluationLab() {
  const index = useData("index.json").data;
  const [repo, setRepo] = useState(null);
  const current = repo || index?.bench?.[0];
  const bench = useData(current ? `bench/${current}.json` : null).data;
  const qa = useData(index?.qa ? "qa.json" : null).data;
  if (!bench) return <div><PageHeader title="Evaluation Lab" /><NoData what="benchmark results" command="python -m bench.run_matrix && python -m bench.analysis.report" /></div>;

  const { dataset, gate, claims } = bench;
  const table = gate.table;

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
