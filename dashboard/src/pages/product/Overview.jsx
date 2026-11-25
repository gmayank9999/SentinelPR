import { Link } from "react-router-dom";
import { Activity, AlertOctagon, CheckCircle2, FolderGit2, GitPullRequest, Hourglass, PlugZap, Rocket, ShieldX, XCircle, Zap } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { ago, repoPath } from "../../lib/format.js";
import { Async, Button, Card, DecisionBadge, EmptyState, PageHeader, RunStatus, Skeleton, Stat, cx } from "../../ui.jsx";

const FEED = {
  ok: { icon: CheckCircle2, tone: "text-ok" },
  warn: { icon: AlertOctagon, tone: "text-warn" },
  bad: { icon: XCircle, tone: "text-bad" },
  info: { icon: Zap, tone: "text-brand" },
};

function Feed() {
  const feed = useApi("/activity?limit=14", { interval: 5000 });
  return (
    <Card title="Recent activity" pad={false}>
      <Async state={feed} skeleton={<div className="space-y-3 p-5"><Skeleton className="h-8" /><Skeleton className="h-8" /><Skeleton className="h-8" /></div>}
        empty={<div className="p-5"><EmptyState icon={Activity} title="No activity yet">Analyse a pull request and its events will appear here as they happen.</EmptyState></div>}>
        {(items) => (
          <ul className="divide-y divide-line">
            {items.map((item, i) => {
              const f = FEED[item.severity] || FEED.info;
              return (
                <li key={i}>
                  <Link to={`/runs/${item.run_id}`} className="flex items-start gap-3 px-5 py-3 transition hover:bg-s2/60">
                    <f.icon size={16} className={cx("mt-0.5 shrink-0", f.tone)} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-ink">{item.title}</span>
                      <span className="block truncate text-xs text-faint">{item.pr_label}{item.detail ? ` · ${item.detail}` : ""}</span>
                    </span>
                    <span className="shrink-0 text-xs text-faint">{ago(item.ts)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Async>
    </Card>
  );
}

export default function Overview() {
  const overview = useApi("/overview", { interval: 8000 });
  const o = overview.data;
  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="What is happening to your pull requests, and what the evidence says about releasing them."
        actions={<>
          <Link to="/repos"><Button icon={PlugZap}>Connect repository</Button></Link>
          <Link to="/pulls"><Button variant="primary" icon={GitPullRequest}>Analyse a pull request</Button></Link>
        </>}
      />
      {overview.error && <div className="mb-6"><Async state={overview}>{() => null}</Async></div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
        <Stat label="Repositories" value={o?.repositories ?? "—"} icon={FolderGit2} to="/repos" />
        <Stat label="Open PRs awaiting analysis" value={o?.awaiting_analysis ?? "—"} icon={Hourglass} to="/pulls" hint={o ? `${o.open_pulls} open in total` : ""} />
        <Stat label="Running or queued" value={o ? o.running + o.queued : "—"} icon={Activity} to="/runs" tone={o && o.running + o.queued ? "text-brand" : "text-ink"} />
        <Stat label="Blocked" value={o?.decisions.BLOCK ?? "—"} icon={ShieldX} tone={o?.decisions.BLOCK ? "text-bad" : "text-ink"} hint="latest decision per PR" />
        <Stat label="Awaiting canary" value={o?.decisions.CANARY ?? "—"} icon={Rocket} tone={o?.decisions.CANARY ? "text-warn" : "text-ink"} hint="latest decision per PR" />
        <Stat label="Passed" value={o?.decisions.PASS ?? "—"} icon={CheckCircle2} tone={o?.decisions.PASS ? "text-ok" : "text-ink"} hint="latest decision per PR" />
      </div>
      {o?.errors?.length > 0 && (
        <div className="mt-4 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-muted">
          Could not list pull requests for {o.errors.map((e) => e.repo_name).join(", ")}: {o.errors[0].message}
        </div>
      )}
      <div className="mt-6 grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-3"><Feed /></div>
        <div className="xl:col-span-2">
          <Card title="Recent release decisions" pad={false}>
            <Async state={overview} skeleton={<div className="space-y-3 p-5"><Skeleton className="h-8" /><Skeleton className="h-8" /></div>}
              empty={null}>
              {(d) => d.recent_runs.length === 0 ? (
                <div className="p-5"><EmptyState icon={GitPullRequest} title="No analyses yet" action={<Link to="/pulls"><Button variant="primary">Choose a pull request</Button></Link>}>
                  Pick a pull request to see the pipeline run and the evidence behind its decision.</EmptyState></div>
              ) : (
                <ul className="divide-y divide-line">
                  {d.recent_runs.map((r) => (
                    <li key={r.run_id}>
                      <Link to={`/runs/${r.run_id}`} className="flex items-center justify-between gap-3 px-5 py-3 hover:bg-s2/60">
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-ink">{r.pr.title || r.pr.ref}</span>
                          <span className="text-xs text-faint">{r.repo_name} · {ago(r.created_at)}</span>
                        </span>
                        {r.decision ? <DecisionBadge decision={r.decision} /> : <RunStatus status={r.status} />}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Async>
          </Card>
        </div>
      </div>
    </div>
  );
}
