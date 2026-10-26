import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Activity, GitCompare } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { ago, duration, prLabel, short } from "../../lib/format.js";
import { Async, Button, Card, DecisionBadge, EmptyState, Id, PageHeader, RunStatus, SkeletonRows } from "../../ui.jsx";

export default function Runs() {
  const runs = useApi("/analyses?limit=200", { interval: 5000 });
  const navigate = useNavigate();
  const [status, setStatus] = useState("all");
  const [decision, setDecision] = useState("all");
  const [picked, setPicked] = useState([]);

  const rows = useMemo(() => (runs.data || []).filter((r) => (status === "all" || r.status === status) && (decision === "all" || r.decision === decision)), [runs.data, status, decision]);
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));
  const compare = () => {
    const [a, b] = picked.map((id) => (runs.data || []).find((r) => r.run_id === id)).sort((x, y) => x.created_at - y.created_at);
    navigate(`/compare?a=${a.run_id}&b=${b.run_id}`);
  };

  return (
    <div>
      <PageHeader title="Analysis Runs" subtitle="Every analysis, kept permanently with its trace. Re-analysing a pull request adds a run; nothing is overwritten."
        actions={<Button icon={GitCompare} variant="primary" disabled={picked.length !== 2} onClick={compare}>Compare selected{picked.length ? ` (${picked.length}/2)` : ""}</Button>} />
      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-3">
          <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 rounded-md border border-line2 bg-bg px-2 text-sm">
            {["all", "queued", "running", "completed", "failed", "cancelled"].map((s) => <option key={s} value={s}>{s === "all" ? "Any status" : s}</option>)}</select>
          <select aria-label="Filter by decision" value={decision} onChange={(e) => setDecision(e.target.value)} className="h-8 rounded-md border border-line2 bg-bg px-2 text-sm">
            {["all", "PASS", "CANARY", "BLOCK"].map((s) => <option key={s} value={s}>{s === "all" ? "Any decision" : s}</option>)}</select>
          <span className="text-xs text-faint">{rows.length} run{rows.length === 1 ? "" : "s"}</span>
        </div>
        <Async state={runs} skeleton={<div className="p-5"><SkeletonRows /></div>}
          empty={<div className="p-5"><EmptyState icon={Activity} title="No analyses yet" action={<Link to="/pulls"><Button variant="primary">Choose a pull request</Button></Link>}>Analyses you start appear here and stay here.</EmptyState></div>}>
          {() => rows.length === 0 ? <div className="p-8 text-center text-sm text-muted">No runs match these filters.</div> : (
            <div className="overflow-x-auto"><table className="table min-w-[820px]"><thead><tr><th className="w-8"><span className="sr-only">Select</span></th><th>Pull request</th><th>Status</th><th>Decision</th><th className="text-right">Risk</th><th>Head</th><th className="text-right">Duration</th><th>When</th></tr></thead><tbody>
              {rows.map((r) => (
                <tr key={r.run_id} className="hover:bg-s2/50">
                  <td><input type="checkbox" aria-label={`Select ${r.run_id} for comparison`} disabled={r.status !== "completed"} checked={picked.includes(r.run_id)} onChange={() => toggle(r.run_id)} className="accent-[var(--color-brand)]" /></td>
                  <td><Link to={`/runs/${r.run_id}`} className="block"><span className="block max-w-80 truncate font-medium text-ink hover:underline">{r.pr.title || prLabel(r.pr)}</span><span className="text-xs text-faint">{r.repo_name} · {prLabel(r.pr)}{r.imported ? " · imported" : ""}</span></Link><Id value={r.run_id} className="text-[11px]" /></td>
                  <td><RunStatus status={r.status} /></td><td>{r.decision ? <DecisionBadge decision={r.decision} /> : <span className="text-faint">—</span>}</td>
                  <td className="mono text-right">{r.risk != null ? r.risk.toFixed(2) : "—"}</td><td className="mono text-muted">{short(r.pr.head_sha)}</td>
                  <td className="mono text-right text-muted">{duration(r.elapsed_s)}</td><td className="text-muted">{ago(r.created_at)}</td>
                </tr>))}
            </tbody></table></div>
          )}
        </Async>
      </Card>
    </div>
  );
}
