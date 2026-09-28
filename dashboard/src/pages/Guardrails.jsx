import { useEffect, useState } from "react";
import { NoData, PageHeader, Stat } from "../components.jsx";
import { loadJSON, pct, useData } from "../data.js";

export default function Guardrails() {
  const index = useData("index.json").data;
  const [events, setEvents] = useState([]);
  const bench = useData(index?.bench?.[0] ? `bench/${index.bench[0]}.json` : null).data;

  useEffect(() => {
    if (!index) return;
    Promise.all(index.runs.slice(0, 40).map((r) => loadJSON(`runs/${r.run_id}.json`).catch(() => null))).then((runs) =>
      setEvents(runs.filter(Boolean).flatMap((r) => (r.guard_events || []).map((e) => ({ ...e, run: r.run_id, pr: r.pr })))),
    );
  }, [index]);

  const byGuard = events.reduce((acc, e) => ({ ...acc, [e.guard]: (acc[e.guard] || 0) + 1 }), {});
  const robust = bench?.robustness;

  return (
    <div>
      <PageHeader title="Guardrails" subtitle="Untrusted PR text is scanned and neutralised before any model sees it, secrets are never sent anywhere, generated text must cite evidence, and the decision can only come from the risk model and policy." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {["injection", "secret", "size", "citation", "schema", "decision-integrity"].map((g) => <Stat key={g} label={g} value={byGuard[g] || 0} hint="events in recent runs" />)}
      </div>
      {robust && (
        <div className="card mt-6">
          <div className="card-title">Adversarial benchmark (RQ6)</div>
          <div className="mb-4 grid gap-4 sm:grid-cols-3">
            <Stat label="Attack success rate" value={pct(robust.attack_success_rate, 1)} hint={`defective + attack PRs that got PASS (n=${robust.attack_prs})`} tone="text-emerald-300" />
            <Stat label="Guard false positives" value={pct(robust.guard_false_positive_rate, 1)} hint="benign PRs flagged" />
            {robust.llm_only_gate_attack_success_rate != null && <Stat label="LLM-only gate" value={pct(robust.llm_only_gate_attack_success_rate, 1)} hint="attacks approved by a single-call LLM reviewer" tone="text-rose-300" />}
          </div>
          <table className="table">
            <thead><tr><th>attack</th><th>PRs</th><th>received PASS</th><th>caught by a guard</th></tr></thead>
            <tbody>{Object.entries(robust.by_attack).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.n}</td><td>{v.passed}</td><td>{v.detected}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      <div className="card mt-6">
        <div className="card-title">Recent guard events</div>
        {events.length === 0 ? <NoData what="guard events" /> : (
          <table className="table">
            <thead><tr><th>guard</th><th>stage</th><th>severity</th><th>message</th><th>where</th><th>PR</th></tr></thead>
            <tbody>
              {events.slice(0, 60).map((e, i) => (
                <tr key={i}>
                  <td>{e.guard}</td><td>{e.stage}</td>
                  <td><span className={`pill ${e.severity === "block" ? "bg-rose-500/15 text-rose-300" : e.severity === "warn" ? "bg-amber-500/15 text-amber-300" : "bg-slate-700/40 text-slate-300"}`}>{e.severity}</span></td>
                  <td className="text-slate-300">{e.message}</td><td className="font-mono text-xs text-slate-500">{e.location || "—"}</td>
                  <td>{e.pr?.number ? `#${e.pr.number}` : e.run.slice(-6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
