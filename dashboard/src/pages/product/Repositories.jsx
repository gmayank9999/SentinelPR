import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Circle, FolderGit2, GitBranch, Link2, Lock, MinusCircle, PlugZap, Search, ShieldCheck, XCircle } from "lucide-react";
import { api, useApi } from "../../lib/api.js";
import { ago } from "../../lib/format.js";
import { Async, Banner, Button, Card, EmptyState, PageHeader, Skeleton, cx, useToast } from "../../ui.jsx";

const LEVEL = {
  full: { label: "Fully supported", tone: "text-ok bg-ok/10" },
  limited: { label: "Limited support", tone: "text-warn bg-warn/10" },
  pending: { label: "Checked after cloning", tone: "text-muted bg-s3" },
  unsupported: { label: "Not supported", tone: "text-bad bg-bad/10" },
};

export function CompatChecks({ compat }) {
  return (
    <ul className="space-y-2 text-sm">
      {compat.checks.map((c) => (
        <li key={c.id} className="flex gap-2.5">
          {c.ok === true ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-ok" aria-label="passed" /> : c.ok === false ? <XCircle size={15} className="mt-0.5 shrink-0 text-bad" aria-label="failed" />
            : <MinusCircle size={15} className="mt-0.5 shrink-0 text-warn" aria-label="cannot be checked" />}
          <span><span className="text-ink">{c.label}</span><span className="block text-muted">{c.detail}</span></span>
        </li>
      ))}
    </ul>
  );
}

export const CompatBadge = ({ level }) => <span className={cx("pill", (LEVEL[level] || LEVEL.pending).tone)}>{(LEVEL[level] || LEVEL.pending).label}</span>;

function Connect({ onConnected }) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [state, setState] = useState({ busy: false, found: null, error: null });

  const check = async (e) => {
    e?.preventDefault();
    setState({ busy: true, found: null, error: null });
    try {
      setState({ busy: false, found: await api("/repos/validate", { method: "POST", body: { url } }), error: null });
    } catch (error) {
      setState({ busy: false, found: null, error });
    }
  };
  const connect = async () => {
    setState((s) => ({ ...s, busy: true }));
    try {
      const repo = await api("/repos", { method: "POST", body: { url } });
      toast.push(`Connected ${repo.full_name}.`, "success");
      setUrl("");
      setState({ busy: false, found: null, error: null });
      onConnected(repo);
    } catch (error) {
      setState((s) => ({ ...s, busy: false }));
      toast.push(error.message, "error");
    }
  };
  const f = state.found;
  return (
    <Card title="Connect a GitHub repository">
      <form onSubmit={check} className="flex flex-col gap-3 sm:flex-row">
        <label className="sr-only" htmlFor="repo-url">GitHub repository URL</label>
        <div className="relative flex-1">
          <Link2 size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input id="repo-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repository"
            className="h-9 w-full rounded-md border border-line2 bg-bg pl-9 pr-3 text-sm outline-none placeholder:text-faint focus:border-brand" autoComplete="off" spellCheck={false} />
        </div>
        <Button variant="secondary" icon={Search} loading={state.busy && !f} disabled={url.trim().length < 3} type="submit">Check access</Button>
      </form>
      {state.error && <p role="alert" className="mt-3 text-sm text-bad">{state.error.message}</p>}
      {f && (
        <div className="rise mt-4 grid gap-5 rounded-lg border border-line bg-s2 p-4 md:grid-cols-2">
          <div>
            <div className="flex items-center gap-2"><FolderGit2 size={16} className="text-brand" aria-hidden /><span className="font-medium">{f.full_name}</span>{f.private && <Lock size={13} className="text-muted" aria-label="private" />}</div>
            <p className="mt-1 text-sm text-muted">{f.description || "No description."}</p>
            <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-faint"><span><GitBranch size={11} className="mr-1 inline" aria-hidden />{f.default_branch}</span><span>{f.language || "no language"}</span><span>{Math.round(f.python_share * 100)}% Python</span>
              <span>{f.authenticated ? "authenticated" : "unauthenticated (public only)"}</span></p>
          </div>
          <div><CompatChecks compat={f.compat} /></div>
          <div className="flex items-center justify-between gap-3 md:col-span-2">
            <p className="text-xs text-faint">Nothing is cloned or executed until you analyse a pull request, and only after you mark the repository trusted.</p>
            {f.already_connected ? <span className="text-sm text-muted">Already connected</span> : <Button variant="primary" icon={PlugZap} loading={state.busy} onClick={connect}>Connect repository</Button>}
          </div>
        </div>
      )}
    </Card>
  );
}

function RepoCard({ repo }) {
  return (
    <Link to={`/repos/${repo.id}`} className="block rounded-lg border border-line bg-s1 p-4 transition hover:border-line2 hover:bg-s2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2"><FolderGit2 size={15} className="shrink-0 text-brand" aria-hidden /><span className="truncate font-medium">{repo.full_name}</span>{repo.private && <Lock size={12} className="text-muted" aria-label="private" />}</div>
          <p className="mt-1 line-clamp-2 text-sm text-muted">{repo.description || "No description."}</p>
        </div>
        <CompatBadge level={repo.compat?.level} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
        <span><GitBranch size={11} className="mr-1 inline" aria-hidden />{repo.default_branch}</span>
        <span>{repo.kind === "demo" ? "bundled demo" : "GitHub"}</span>
        <span className={cx("inline-flex items-center gap-1", repo.trusted ? "text-ok" : "text-warn")}>{repo.trusted ? <ShieldCheck size={11} aria-hidden /> : <Circle size={11} aria-hidden />}{repo.trusted ? "trusted" : "not trusted"}</span>
        {repo.last_synced_at && <span>synced {ago(repo.last_synced_at)}</span>}
        {repo.status === "needs_seed" && <span className="text-warn">demo history not seeded</span>}
      </div>
    </Link>
  );
}

export default function Repositories() {
  const repos = useApi("/repos");
  return (
    <div>
      <PageHeader title="Repositories" subtitle="Connect a GitHub repository, then pick one of its pull requests to analyse. The bundled UniERP demo is always available." />
      <Connect onConnected={repos.reload} />
      <h2 className="mb-3 mt-8 text-xs font-medium uppercase tracking-wider text-faint">Connected</h2>
      <Async state={repos} skeleton={<div className="grid gap-3 md:grid-cols-2"><Skeleton className="h-28" /><Skeleton className="h-28" /></div>}
        empty={<EmptyState icon={FolderGit2} title="No repositories connected">Paste a GitHub URL above.</EmptyState>}>
        {(list) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{list.map((r) => <RepoCard key={r.id} repo={r} />)}</div>
        )}
      </Async>
      <div className="mt-6"><Banner title="How access works">
        SentinelPR reads GitHub with a token configured on the server (<span className="mono">GITHUB_TOKEN</span>), so private repositories work only if that token can read them.
        There is no GitHub sign-in in this version; see Integration Settings.</Banner></div>
    </div>
  );
}
