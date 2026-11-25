import { Link } from "react-router-dom";
import { GitBranch, GitPullRequest, Play, RefreshCw } from "lucide-react";
import { ago, repoPath } from "../lib/format.js";
import { Button, DecisionBadge, RunStatus } from "../ui.jsx";
import { useAnalyze } from "../lib/analyze.jsx";

/** Open pull requests with their latest SentinelPR decision and an Analyze action. */
export default function PullTable({ pulls, repos, showRepo = false }) {
  const { start, busy, dialog } = useAnalyze();
  const byId = Object.fromEntries((repos || []).map((r) => [r.id, r]));
  return (
    <>
      <div className="overflow-x-auto">
        <table className="table min-w-[760px]">
          <thead>
            <tr><th>Pull request</th>{showRepo && <th>Repository</th>}<th>Author</th><th>Changes</th><th>Latest analysis</th><th className="text-right">Action</th></tr>
          </thead>
          <tbody>
            {pulls.map((p) => {
              const ref = p.number ?? p.ref;
              const run = p.latest_run;
              const repo = byId[p.repo_id] || { id: p.repo_id, full_name: p.repo_name };
              return (
                <tr key={`${p.repo_id}:${ref}`} className="hover:bg-s2/50">
                  <td>
                    <Link to={repoPath(p.repo_id, ref)} className="group flex items-start gap-2">
                      {p.number != null ? <GitPullRequest size={15} className="mt-0.5 shrink-0 text-ok" aria-hidden /> : <GitBranch size={15} className="mt-0.5 shrink-0 text-brand" aria-hidden />}
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-ink group-hover:underline">{p.title}</span>
                        <span className="mono text-faint">{p.number != null ? `#${p.number} · ` : ""}{p.head_ref} → {p.base_ref}{p.draft ? " · draft" : ""}</span>
                      </span>
                    </Link>
                  </td>
                  {showRepo && <td className="text-muted">{p.repo_name}</td>}
                  <td className="text-muted">{p.author || "—"}</td>
                  <td className="text-muted">{p.summary || (p.changed_files != null ? `${p.changed_files} files · +${p.additions ?? 0} −${p.deletions ?? 0}` : "—")}</td>
                  <td>
                    {run ? (
                      <Link to={`/runs/${run.run_id}`} className="flex flex-col gap-1">
                        <span className="flex items-center gap-2">{run.decision ? <DecisionBadge decision={run.decision} /> : <RunStatus status={run.status} />}
                          {p.stale && <span className="pill bg-warn/10 text-warn" title="The pull request changed after this analysis">stale</span>}</span>
                        <span className="text-xs text-faint">{ago(run.created_at)}{run.risk != null ? ` · risk ${run.risk.toFixed(2)}` : ""}</span>
                      </Link>
                    ) : <span className="text-sm text-faint">Not analysed</span>}
                  </td>
                  <td className="text-right">
                    <Button size="sm" variant={run && !p.stale ? "secondary" : "primary"} icon={run ? RefreshCw : Play} loading={busy === `${p.repo_id}:${ref}`}
                      disabled={run && (run.status === "running" || run.status === "queued")} onClick={() => start(repo, ref)}>
                      {run ? "Re-analyse" : "Analyse"}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {dialog}
    </>
  );
}
