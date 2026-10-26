import { useApi } from "../../lib/api.js";
import { num, pct, stamp } from "../../lib/format.js";
import { Async, Banner, KV } from "../../ui.jsx";

/** The deployed risk model as documented by its own file and the committed benchmark results. */
export default function ModelCard() {
  const model = useApi("/model");
  return (
    <Async state={model} empty={null}>
      {(m) => (
        <div className="space-y-4">
          <Banner tone={m.family === "expert" ? "warn" : "info"} title={m.family === "expert" ? "Hand-weighted model, not a learned one" : "Fitted model"}>
            {m.weights_origin}. Calibration used {m.calibration.pr_count ?? "?"} benchmark PRs ({m.calibration.positives ?? "?"} defective), calibrated on {m.calibration.calibrated_on || "—"}. {m.threshold_rule}.
          </Banner>
          <KV rows={[["Version", m.name], ["Kind", `${m.kind}, ${m.feature_count} features`], ["Thresholds", `canary ${m.thresholds.canary} · block ${m.thresholds.block}`], ["Trained", m.trained_at ? stamp(Date.parse(m.trained_at) / 1000) : "—"]]} />
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Evaluation evidence (committed benchmark results)</h4>
            <div className="overflow-x-auto"><table className="table"><thead><tr><th>Repository</th><th>PRs (scored out of fold)</th><th className="text-right">PR-AUC</th><th className="text-right">False blocks</th><th className="text-right">"Tests pass" PR-AUC</th></tr></thead><tbody>
              {m.evaluations.map((e) => <tr key={e.repo}><td>{e.repo}</td><td>{e.prs} ({e.scored_out_of_fold})</td><td className="mono text-right">{num(e.sentinel.pr_auc, 3)}</td><td className="mono text-right">{pct(e.sentinel.false_block_rate, 1)}</td><td className="mono text-right text-muted">{num(e.merge_if_tests_pass.pr_auc, 3)}</td></tr>)}
            </tbody></table></div>
            <p className="mt-2 text-xs text-faint">These describe benchmark PRs. They say nothing about whether an individual PR is safe; that comes only from its own analysis.</p>
          </div>
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Known limitations ({m.limitations_source})</h4>
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted">{m.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </div>
        </div>
      )}
    </Async>
  );
}

export function WeightsTable() {
  const model = useApi("/model");
  return (
    <Async state={model} empty={null}>
      {(m) => (
        <div className="overflow-x-auto"><table className="table"><thead><tr><th>Feature</th><th>Group</th><th>Allowed direction</th><th className="text-right">Weight per unit</th></tr></thead><tbody>
          {m.features.filter((f) => f.coefficient !== 0).sort((a, b) => Math.abs(b.per_unit) - Math.abs(a.per_unit)).map((f) => (
            <tr key={f.name}><td><span className="mono">{f.name}</span><div className="text-xs text-faint">{f.label}</div></td><td className="text-muted">{f.group}</td>
              <td className="text-muted">{f.direction > 0 ? "raises risk" : f.direction < 0 ? "lowers risk" : "free"}</td><td className="mono text-right">{f.per_unit > 0 ? "+" : ""}{num(f.per_unit, 3)}</td></tr>))}
        </tbody></table></div>
      )}
    </Async>
  );
}
