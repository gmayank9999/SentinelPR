import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FolderGit2, GitPullRequest, Search } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { Async, Banner, Button, Card, EmptyState, PageHeader, SkeletonRows } from "../../ui.jsx";
import PullTable from "../../components/PullTable.jsx";

const FILTERS = [
  ["all", "All"],
  ["awaiting", "Awaiting analysis"],
  ["BLOCK", "Blocked"],
  ["CANARY", "Canary"],
  ["PASS", "Passed"],
];

export default function PullRequests() {
  const pulls = useApi("/pulls");
  const repos = useApi("/repos");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  const items = useMemo(() => {
    const list = pulls.data?.pulls || [];
    const q = query.trim().toLowerCase();
    return list.filter((p) => {
      if (q && !`${p.title} ${p.head_ref} ${p.author} ${p.repo_name} ${p.number ?? ""}`.toLowerCase().includes(q)) return false;
      if (filter === "awaiting") return !p.latest_run || p.stale;
      if (filter === "all") return true;
      return p.latest_run?.decision === filter;
    });
  }, [pulls.data, query, filter]);

  return (
    <div>
      <PageHeader title="Pull Requests" subtitle="Open pull requests across your repositories, with the latest SentinelPR decision for each." />
      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-3">
          <div className="relative min-w-52 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, branch, author…" aria-label="Search pull requests"
              className="h-8 w-full rounded-md border border-line2 bg-bg pl-8 pr-3 text-sm outline-none placeholder:text-faint focus:border-brand" />
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter">
            {FILTERS.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={filter === id} onClick={() => setFilter(id)}
                className={`rounded-md px-2.5 py-1 text-xs ${filter === id ? "bg-s3 text-ink" : "text-muted hover:bg-s2"}`}>{label}</button>
            ))}
          </div>
        </div>
        <Async state={pulls} skeleton={<div className="p-5"><SkeletonRows /></div>}
          empty={null}>
          {(data) => data.pulls.length === 0 ? (
            <div className="p-5"><EmptyState icon={GitPullRequest} title="No open pull requests"
              action={<Link to="/repos"><Button variant="primary" icon={FolderGit2}>Connect a repository</Button></Link>}>
              {data.errors.length ? `Some repositories could not be read: ${data.errors[0].message}` : "Connect a repository, or seed the demo branches with python demo/scenarios.py."}</EmptyState></div>
          ) : items.length === 0 ? (
            <div className="p-5"><EmptyState icon={Search} title="Nothing matches">Try another search or filter.</EmptyState></div>
          ) : <PullTable pulls={items} repos={repos.data || []} showRepo />}
        </Async>
      </Card>
      {pulls.data?.errors?.length > 0 && <div className="mt-4"><Banner tone="warn" title="Some repositories could not be listed">{pulls.data.errors.map((e) => `${e.repo_name}: ${e.message}`).join(" · ")}</Banner></div>}
    </div>
  );
}
