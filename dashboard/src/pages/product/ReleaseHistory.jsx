import { useMemo } from "react";
import { Link } from "react-router-dom";
import { History, Rocket } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { prLabel, short } from "../../lib/format.js";
import { Async, DecisionBadge, EmptyState, PageHeader, SkeletonRows } from "../../ui.jsx";

const ACTION = {
  PASS: "Merge normally.",
  CANARY: "Merge, then release through the canary rollout with automatic rollback.",
  BLOCK: "Do not merge until the blocking reason is addressed.",
};

/** The release decisions in order, by day: what the evidence said and what it means for shipping. */
export default function ReleaseHistory() {
  const runs = useApi("/analyses?status=completed&limit=200");
  const days = useMemo(() => {
    const groups = {};
    (runs.data || []).filter((r) => r.decision).forEach((r) => {
      const day = new Date(r.created_at * 1000).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
      (groups[day] = groups[day] || []).push(r);
    });
    return Object.entries(groups);
  }, [runs.data]);
  return (
    <div>
      <PageHeader title="Release History" subtitle="Each analysis ends in a release decision. This is the ledger of those decisions and what each one means for shipping."
        actions={<Link to="/canary" className="text-sm text-brand hover:underline"><Rocket size={13} className="mr-1 inline" aria-hidden />Canary deployments</Link>} />
      <Async state={runs} skeleton={<SkeletonRows />} empty={<EmptyState icon={History} title="No release decisions yet">Decisions appear here after an analysis completes.</EmptyState>}>
        {() => (
          <div className="space-y-8">
            {days.map(([day, items]) => (
              <section key={day}>
                <h2 className="mb-3 text-xs font-medium uppercase tracking-wider text-faint">{day}</h2>
                <ol className="space-y-px overflow-hidden rounded-lg border border-line">
                  {items.map((r) => (
                    <li key={r.run_id}><Link to={`/runs/${r.run_id}`} className="grid grid-cols-[auto_minmax(0,1fr)] gap-4 bg-s1 px-5 py-3.5 hover:bg-s2 md:grid-cols-[110px_minmax(0,1fr)_minmax(0,1fr)]">
                      <span><DecisionBadge decision={r.decision} /><div className="mono mt-1 text-xs text-faint">risk {r.risk?.toFixed(2)}</div></span>
                      <span className="min-w-0"><span className="block truncate text-sm font-medium">{r.pr.title || prLabel(r.pr)}</span><span className="text-xs text-faint">{r.repo_name} · {prLabel(r.pr)} · {short(r.pr.head_sha)}</span></span>
                      <span className="col-span-2 text-sm text-muted md:col-span-1">{ACTION[r.decision]}</span>
                    </Link></li>))}
                </ol>
              </section>))}
          </div>
        )}
      </Async>
    </div>
  );
}
