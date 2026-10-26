import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, GitBranch, GitPullRequest, RefreshCw, ShieldCheck, ShieldOff, Trash2 } from "lucide-react";
import { api, useApi } from "../../lib/api.js";
import { useCrumbs } from "../../Shell.jsx";
import { Async, Banner, Button, Card, DecisionBadge, EmptyState, ErrorState, Modal, PageHeader, RunStatus, SkeletonRows, useToast } from "../../ui.jsx";
import { ago } from "../../lib/format.js";
import PullTable from "../../components/PullTable.jsx";
import { CompatBadge, CompatChecks } from "./Repositories.jsx";

export default function RepoDetail() {
  const { repoId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const repo = useApi(`/repos/${repoId}`);
  const pulls = useApi(`/repos/${repoId}/pulls`);
  const runs = useApi(`/analyses?repo_id=${encodeURIComponent(repoId)}&limit=8`, { interval: 5000 });
  const [removing, setRemoving] = useState(false);
  useCrumbs([{ label: "Repositories", to: "/repos" }, { label: repo.data?.full_name || repoId }]);

  if (repo.loading) return <SkeletonRows rows={4} />;
  if (repo.error) return <ErrorState error={repo.error} onRetry={repo.refetch} title="Repository not found" />;
  const r = repo.data;

  const toggleTrust = async () => {
    try {
      await api(`/repos/${r.id}/trust`, { method: "POST", body: { trusted: !r.trusted } });
      repo.reload();
      toast.push(r.trusted ? "Trust removed." : "Repository trusted.", "success");
    } catch (e) {
      toast.push(e.message, "error");
    }
  };
  const seed = async () => {
    try {
      await api(`/repos/${r.id}/seed`, { method: "POST" });
      toast.push("Seeding the demo history (about a minute)…", "info");
      setTimeout(() => repo.reload(), 4000);
    } catch (e) {
      toast.push(e.message, "error");
    }
  };
  const remove = async () => {
    try {
      await api(`/repos/${r.id}`, { method: "DELETE" });
      toast.push("Repository disconnected.", "success");
      navigate("/repos");
    } catch (e) {
      toast.push(e.message, "error");
    }
  };

  return (
    <div>
      <PageHeader
        back={<Link to="/repos" className="mb-2 inline-flex items-center gap-1 text-xs text-muted hover:text-ink"><ArrowLeft size={12} /> Repositories</Link>}
        title={r.full_name}
        subtitle={r.description}
        actions={<>
          {r.url && <a href={r.url} target="_blank" rel="noreferrer"><Button icon={ExternalLink}>GitHub</Button></a>}
          <Button icon={RefreshCw} onClick={() => pulls.refetch()}>Refresh</Button>
          {r.kind === "github" && <Button variant="ghost" icon={Trash2} onClick={() => setRemoving(true)}>Disconnect</Button>}
        </>}
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Compatibility" className="lg:col-span-2" action={<CompatBadge level={r.compat.level} />}>
          <CompatChecks compat={r.compat} />
          {r.project?.package && <p className="mono mt-3 text-faint">package {r.project.package} · tests {r.project.tests}/</p>}
        </Card>
        <Card title="Where its code runs">
          <div className="flex items-center gap-2 text-sm">{r.kind === "demo" ? <ShieldOff size={16} className="text-muted" aria-hidden /> : <ShieldCheck size={16} className="text-ok" aria-hidden />}
            <span className="font-medium">{r.kind === "demo" ? "On this machine (bundled demo)" : "In a Docker sandbox"}</span></div>
          <p className="mt-2 text-sm text-muted">{r.kind === "demo" ? "The demo is part of this project, so it runs directly." : "Tests and mutants run in containers with no network, a read-only system and resource limits. If Docker is not running, analysis is refused unless you explicitly trust the repository to run unsandboxed."}</p>
          {r.kind === "github" && <Button className="mt-3" size="sm" variant={r.trusted ? "danger" : "secondary"} onClick={toggleTrust}>{r.trusted ? "Remove unsandboxed trust" : "Allow unsandboxed fallback"}</Button>}
          {r.kind === "github" && r.trusted && <p className="mt-2 text-xs text-warn">Unsandboxed execution is allowed only if Docker is unavailable.</p>}
        </Card>
      </div>

      <h2 className="mb-3 mt-8 flex items-center gap-2 text-sm font-medium"><GitPullRequest size={15} aria-hidden /> Pull requests</h2>
      {r.status === "needs_seed" || r.status === "seeding" ? (
        <EmptyState icon={GitBranch} title={r.status === "seeding" ? "Seeding the demo repository…" : "The demo repository has not been seeded"} action={r.status === "needs_seed" && <Button variant="primary" onClick={seed}>Seed demo history</Button>}>
          Seeding rebuilds UniERP's scripted commit history locally.</EmptyState>
      ) : (
        <Card pad={false}>
          <Async state={pulls} skeleton={<div className="p-5"><SkeletonRows /></div>}
            empty={<div className="p-5"><EmptyState icon={GitPullRequest} title={r.kind === "demo" ? "No demo branches" : "No open pull requests"}>
              {r.kind === "demo" ? <>Create demo pull requests with <span className="mono">python demo/scenarios.py</span>.</> : "This repository has no open pull requests."}</EmptyState></div>}>
            {(items) => <PullTable pulls={items.map((p) => ({ ...p, repo_id: r.id, repo_name: r.full_name }))} repos={[r]} />}
          </Async>
        </Card>
      )}

      <h2 className="mb-3 mt-8 text-sm font-medium">Recent analyses</h2>
      <Card pad={false}>
        <Async state={runs} empty={<div className="p-5 text-sm text-muted">No analyses for this repository yet.</div>} skeleton={<div className="p-5"><SkeletonRows rows={2} /></div>}>
          {(items) => (
            <ul className="divide-y divide-line">
              {items.map((m) => (
                <li key={m.run_id}><Link to={`/runs/${m.run_id}`} className="flex items-center justify-between gap-3 px-5 py-3 hover:bg-s2/60">
                  <span className="min-w-0 truncate text-sm">{m.pr.title || m.pr.ref} <span className="text-faint">· {ago(m.created_at)}</span></span>
                  {m.decision ? <DecisionBadge decision={m.decision} /> : <RunStatus status={m.status} />}
                </Link></li>
              ))}
            </ul>
          )}
        </Async>
      </Card>

      <Modal open={removing} title={`Disconnect ${r.full_name}?`} onClose={() => setRemoving(false)}
        footer={<><Button variant="ghost" onClick={() => setRemoving(false)}>Cancel</Button><Button variant="danger" onClick={remove}>Disconnect</Button></>}>
        <Banner tone="warn">Past analysis runs are kept. The local clone stays on disk until you delete it.</Banner>
      </Modal>
    </div>
  );
}
