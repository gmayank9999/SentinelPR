import { Link } from "react-router-dom";
import { DecisionBadge, NoData, PageHeader, Stat } from "../components.jsx";
import { num, pct, useData } from "../data.js";

export default function Overview() {
  const index = useData("index.json");
  const runs = index.data?.runs || [];
  const benchName = index.data?.bench?.[0];
  const bench = useData(benchName ? `bench/${benchName}.json` : null).data;
  const counts = runs.reduce((acc, r) => ({ ...acc, [r.decision]: (acc[r.decision] || 0) + 1 }), {});
  const gate = bench?.gate?.table?.SentinelPR;

  return (
    <div>
      <PageHeader
        title="SentinelPR"
        subtitle="Agents make claims about each pull request, execution verifies them, a calibrated model scores the verified evidence, and the gate decides: PASS, CANARY or BLOCK."
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Analysed PRs" value={runs.length} hint={`${counts.PASS || 0} pass · ${counts.CANARY || 0} canary · ${counts.BLOCK || 0} block`} />
        <Stat label="Benchmark PRs" value={bench?.dataset?.prs ?? "—"} hint={bench ? `${bench.dataset.defective} defective by execution` : "run the benchmark"} />
        <Stat label="Gate PR-AUC" value={num(gate?.pr_auc)} hint={gate ? `ROC-AUC ${num(gate.roc_auc)}` : ""} tone="text-emerald-300" />
        <Stat label="False blocks" value={pct(gate?.false_block_rate, 1)} hint="benign PRs blocked (target ≤ 5%)" />
      </div>

      <div className="card mt-6">
        <div className="card-title">Recent runs</div>
        {runs.length === 0 ? (
          <NoData command="python -m sentinel.publish.dashboard_data" />
        ) : (
          <table className="table">
            <thead>
              <tr><th>PR</th><th>Decision</th><th>Risk</th><th>Time</th><th>When</th></tr>
            </thead>
            <tbody>
              {runs.slice(0, 15).map((r) => (
                <tr key={r.run_id}>
                  <td>
                    <Link to={`/runs/${r.run_id}`} className="text-slate-200 hover:text-emerald-300">
                      {r.pr?.number ? `#${r.pr.number} ` : ""}{r.pr?.title || r.run_id}
                    </Link>
                  </td>
                  <td><DecisionBadge decision={r.decision} /></td>
                  <td className="font-mono">{num(r.risk)}</td>
                  <td>{num(r.elapsed_s, 1)}s</td>
                  <td className="text-slate-500">{r.created_at?.slice(0, 16).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
