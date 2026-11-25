import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { num, short } from "../../lib/format.js";
import { useCrumbs } from "../../Shell.jsx";
import { Async, Banner, Card, ClaimStatus, DecisionBadge, EmptyState, PageHeader, SkeletonRows } from "../../ui.jsx";

const Side = ({ s, label }) => (
  <div className="flex-1 rounded-lg border border-line bg-s2 p-4">
    <div className="text-xs text-faint">{label}</div>
    <Link to={`/runs/${s.run_id}`} className="mono text-xs text-brand hover:underline">{s.run_id}</Link>
    <div className="mt-2 flex items-center gap-3"><DecisionBadge decision={s.decision} size="lg" /><span className="text-2xl font-semibold tabular-nums">{num(s.risk)}</span></div>
    <div className="mono mt-2 text-xs text-faint">head {short(s.head_sha)}</div>
  </div>
);

const List = ({ title, items, render, empty }) => (
  <Card title={`${title} (${items.length})`}>{items.length ? <ul className="space-y-2 text-sm">{items.map((x, i) => <li key={i}>{render(x)}</li>)}</ul> : <p className="text-sm text-muted">{empty}</p>}</Card>
);

export default function Compare() {
  const [params] = useSearchParams();
  const [a, b] = [params.get("a"), params.get("b")];
  const diff = useApi(a && b ? `/analyses/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}` : null);
  useCrumbs([{ label: "Analysis Runs", to: "/runs" }, { label: "Compare" }]);
  if (!a || !b) return <EmptyState title="Choose two runs to compare">Select two completed runs on the Analysis Runs page.</EmptyState>;
  return (
    <div>
      <PageHeader title="Compare analyses" subtitle="What changed between two runs: decision, evidence, tests and configuration." />
      <Async state={diff} skeleton={<SkeletonRows />}>
        {(d) => (
          <div className="space-y-6">
            <div className="flex flex-col items-stretch gap-3 md:flex-row md:items-center"><Side s={d.a} label="Earlier" /><ArrowRight className="mx-auto shrink-0 text-faint" size={20} aria-hidden /><Side s={d.b} label="Later" /></div>
            {!d.same_pr && <Banner tone="warn" title="These runs are for different pull requests">The comparison still lists differences, but they are not revisions of the same change.</Banner>}
            <Banner title={d.decision_changed ? `Decision changed from ${d.a.decision} to ${d.b.decision}` : `Decision unchanged (${d.a.decision})`}>
              Risk {d.risk_delta == null ? "n/a" : `${d.risk_delta > 0 ? "rose" : d.risk_delta < 0 ? "fell" : "did not change"}${d.risk_delta ? ` by ${Math.abs(d.risk_delta).toFixed(3)}` : ""}`}. {d.same_head ? "Both runs analysed the same commit." : "The runs analysed different commits."}
              {d.mutation_score.a != null && d.mutation_score.b != null && ` Mutation score ${num(d.mutation_score.a)} → ${num(d.mutation_score.b)}.`}</Banner>
            <div className="grid gap-4 lg:grid-cols-2">
              <List title="Claims whose status changed" items={d.claims.status_changed} empty="No claim changed status." render={(c) => <><span className="mono break-all">{c.target}</span><div className="mt-1 flex items-center gap-2"><ClaimStatus status={c.from} /><ArrowRight size={12} aria-hidden /><ClaimStatus status={c.to} /></div></>} />
              <List title="New or removed claims" items={[...d.claims.added.map((c) => ({ ...c, kind: "added" })), ...d.claims.removed.map((c) => ({ ...c, kind: "removed" }))]} empty="The same claims were made." render={(c) => <><span className={c.kind === "added" ? "text-ok" : "text-bad"}>{c.kind === "added" ? "+" : "−"}</span> <span className="mono break-all">{c.target}</span> <ClaimStatus status={c.status} /></>} />
              <List title="Tests" items={[...d.tests.newly_failing.map((t) => ({ t, k: "now failing" })), ...d.tests.fixed.map((t) => ({ t, k: "fixed" })), ...d.tests.selected_added.map((t) => ({ t, k: "newly selected" })), ...d.tests.selected_removed.map((t) => ({ t, k: "no longer selected" }))]} empty="Same test outcomes and selection." render={(x) => <><span className="pill bg-s3 text-muted">{x.k}</span> <span className="mono break-all">{x.t}</span></>} />
              <List title="Configuration differences" items={d.config} empty="Same model, thresholds, prompts and LLM mode." render={(c) => <><span className="text-muted">{c.key}</span><div className="mono text-xs break-all">{JSON.stringify(c.a)} → {JSON.stringify(c.b)}</div></>} />
            </div>
            <List title="Features that changed" items={d.features} empty="Every feature had the same value." render={(f) => <span className="flex justify-between gap-4"><span className="mono">{f.feature}</span><span className="mono text-muted">{num(f.a, 3)} → {num(f.b, 3)}</span></span>} />
          </div>
        )}
      </Async>
    </div>
  );
}
