import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, MessageSquare, MinusCircle, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { api, useApi } from "../../lib/api.js";
import { short } from "../../lib/format.js";
import { Banner, Button, CopyButton, Modal, cx, useToast } from "../../ui.jsx";

function Row({ icon: Icon, tone, title, children }) {
  return (
    <li className="flex gap-3 rounded-md border border-line p-3">
      <Icon size={17} className={cx("mt-0.5 shrink-0", tone)} aria-hidden />
      <div className="min-w-0 text-sm"><div className="font-medium text-ink">{title}</div><div className="text-muted">{children}</div></div>
    </li>
  );
}

function Why({ item }) {
  return (
    <>
      <span>{item.explanation}</span>
      {item.github_message && <span className="mt-1 block text-xs text-faint">GitHub said: “{item.github_message}”{item.accepted_permissions ? ` · needs ${item.accepted_permissions}` : ""}</span>}
      {item.fix && <span className="mt-1 block text-ink">What to do: {item.fix}</span>}
    </>
  );
}

/** What GitHub actually confirmed. The comment, the commit status and the Check Run are reported separately. */
export function PublishResult({ result }) {
  const { comment, status, check_run: check } = result;
  return (
    <div className="space-y-3" aria-live="polite">
      {result.published
        ? <Banner title={`Published to ${result.repository} #${result.pr_number}`}>For commit {short(result.head_sha)}. Each part below was confirmed or refused by GitHub on its own.</Banner>
        : <Banner tone="bad" title="Nothing was published">{result.error || result.reason || "GitHub refused everything that was attempted."}</Banner>}
      <ul className="space-y-2">
        {comment.posted
          ? <Row icon={CheckCircle2} tone="text-ok" title="Pull request comment: posted">
            <a className="inline-flex items-center gap-1 text-brand hover:underline" href={comment.url} target="_blank" rel="noreferrer">Open the comment <ExternalLink size={12} aria-hidden /></a></Row>
          : comment.attempted === false ? <Row icon={MinusCircle} tone="text-faint" title="Pull request comment: not selected">Skipped at your request.</Row>
            : <Row icon={XCircle} tone="text-bad" title="Pull request comment: not posted">{comment.error != null ? <Why item={comment} /> : (result.error || result.reason || "No comment was created.")}</Row>}
        {status.posted
          ? <Row icon={CheckCircle2} tone="text-ok" title="Commit status: posted">A status was set on commit {short(result.head_sha)}. It can be made a required check in branch protection.</Row>
          : status.attempted === false ? <Row icon={MinusCircle} tone="text-faint" title="Commit status: not selected">Skipped at your request.</Row>
            : <Row icon={XCircle} tone="text-bad" title="Commit status: not posted">{status.error != null ? <Why item={status} /> : "No status was created."}</Row>}
        {check.created
          ? <Row icon={CheckCircle2} tone="text-ok" title="Check Run: created">GitHub returned check run id {check.id}.</Row>
          : check.attempted === false
            ? <Row icon={MinusCircle} tone="text-faint" title="Check Run: not attempted">{check.explanation ? <Why item={check} /> : "No request was sent."}</Row>
            : <Row icon={XCircle} tone="text-warn" title="Check Run: refused by GitHub">{check.error != null ? <Why item={check} /> : "No Check Run was created."}</Row>}
      </ul>
    </div>
  );
}

function Target({ id, label, checked, onChange, disabled, children }) {
  return (
    <li className={cx("rounded-md border p-3", disabled ? "border-line opacity-80" : "border-line2")}>
      <label className="flex cursor-pointer items-start gap-2.5">
        <input type="checkbox" checked={checked && !disabled} disabled={disabled} onChange={(e) => onChange(id, e.target.checked)} className="mt-1 accent-[var(--color-brand)]" />
        <span className="min-w-0 text-sm"><span className="font-medium text-ink">{label}</span><span className="block text-muted">{children}</span></span>
      </label>
    </li>
  );
}

/**
 * "Publish to GitHub" for a finished analysis. It names the exact repository, pull request and commit, says which of the three
 * things (comment, commit status, Check Run) can be posted and why not, and only then posts what is selected.
 */
export default function PublishPanel({ runId, meta, onPublished }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [force, setForce] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [pick, setPick] = useState({ comment: true, status: true, check_run: true });
  const info = useApi(open ? `/analyses/${encodeURIComponent(runId)}/publish` : null);
  const d = info.data;
  const previously = meta?.publish?.published;
  const targets = d?.targets;
  const checkOk = Boolean(targets?.check_run?.available);
  useEffect(() => { if (targets && !checkOk) setPick((p) => ({ ...p, check_run: false })); }, [targets, checkOk]);

  const close = () => { if (!busy) { setOpen(false); setResult(null); setError(null); setForce(false); } };
  const toggle = (id, on) => setPick((p) => ({ ...p, [id]: on }));
  const chosen = { comment: pick.comment, status: pick.status, check_run: pick.check_run && checkOk };
  const nothing = !Object.values(chosen).some(Boolean);

  const post = async () => {
    if (busy) return; // a second click while a request is in flight does nothing
    setBusy(true);
    setError(null);
    try {
      const out = await api(`/analyses/${encodeURIComponent(runId)}/publish`, { method: "POST", body: { force, targets: chosen } });
      setResult(out);
      const parts = [out.comment.posted && "comment", out.status.posted && "commit status", out.check_run.created && "Check Run"].filter(Boolean);
      toast.push(out.published ? `Posted: ${parts.join(", ")}.` : "Nothing was published.", out.published ? "success" : "error");
      onPublished?.(out);
      info.reload();
    } catch (e) {
      setError(e);
      toast.push(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const blocked = d && d.blockers.length > 0;
  const needsForce = d?.already_published && !result;
  return (
    <>
      <Button size="sm" icon={MessageSquare} onClick={() => setOpen(true)}>{previously ? "Published · publish again…" : "Publish to GitHub"}</Button>
      <Modal open={open} onClose={close} title="Publish this result to GitHub"
        footer={result ? <Button variant="primary" onClick={close}>Done</Button> : <>
          <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
          <Button variant="primary" icon={ShieldCheck} loading={busy} disabled={!d || blocked || nothing || (needsForce && !force)} onClick={post}>Post to GitHub</Button></>}>
        {info.loading && !d && <p className="text-muted">Checking what publishing needs…</p>}
        {info.error && <Banner tone="bad" title="Could not check publishing">{info.error.message}</Banner>}
        {d && !result && (
          <div className="space-y-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-faint">Repository</dt><dd className="mono">{d.target.repository}</dd>
              <dt className="text-faint">Pull request</dt><dd className="mono">#{d.target.pr_number}</dd>
              <dt className="text-faint">Commit analysed</dt><dd className="mono">{short(d.target.head_sha)}</dd>
              <dt className="text-faint">Decision</dt><dd>{d.target.decision}</dd>
            </dl>
            {d.stale && <Banner tone="warn" title="The pull request has changed since this analysis">It is posted for the commit that was analysed ({short(d.target.head_sha)}), not the PR's current head. Re-analyse first if you want its latest state.</Banner>}
            {blocked && (
              <div className="space-y-3" role="alert">
                <p className="flex items-center gap-2 text-sm font-medium text-warn"><ShieldAlert size={15} aria-hidden /> Publishing is not possible yet</p>
                {d.blockers.map((b) => (
                  <div key={b.code} className="rounded-md border border-warn/30 bg-warn/5 p-3 text-sm">
                    <div className="text-ink">{b.message}</div><div className="mt-1 text-muted">{b.fix}</div>
                    {b.code === "disabled" && <div className="mt-2 flex items-center gap-2"><code className="mono rounded bg-bg px-2 py-1 text-brand">SENTINEL_ALLOW_PUBLISH=1</code><CopyButton text="SENTINEL_ALLOW_PUBLISH=1" label="Copy line" /></div>}
                  </div>
                ))}
                <p className="text-xs text-faint">Restart with <span className="mono">.venv\Scripts\python -m sentinel.api --port 8765</span>, then press Check again.</p>
                <Button size="sm" onClick={info.refetch}>Check again</Button>
              </div>
            )}
            {!blocked && targets && (
              <div>
                <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">What to post</h4>
                <ul className="space-y-2">
                  <Target id="comment" label="Pull request comment" checked={pick.comment} onChange={toggle}>{d.will_post.comment} <span className="text-faint">Uses your personal token ({targets.comment.needs}).</span></Target>
                  <Target id="status" label={`Commit status “${targets.status.context}”`} checked={pick.status} onChange={toggle}>{d.will_post.status} <span className="text-faint">Uses your personal token ({targets.status.needs}).</span></Target>
                  <Target id="check_run" label="Check Run" checked={pick.check_run} onChange={toggle} disabled={!checkOk}>
                    {checkOk
                      ? <>Created as the GitHub App <b className="text-ink">{targets.check_run.app?.name}</b> with a one-hour token limited to this repository and Checks: write.</>
                      : <>
                        <span className="text-warn">Not available: GitHub only lets a GitHub App create Check Runs; a personal access token cannot.</span>
                        {targets.check_run.problems.map((p) => <span key={p} className="mt-0.5 block">{p}</span>)}
                        <span className="mt-0.5 block text-faint">To enable it, follow docs/github-app.md. Until then the commit status gives you a merge gate with your personal token.</span></>}
                  </Target>
                </ul>
                {nothing && <p className="mt-2 text-xs text-warn">Select at least one thing to post.</p>}
              </div>
            )}
            {needsForce && (
              <label className="flex cursor-pointer items-start gap-2 text-sm text-muted">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="mt-0.5 accent-[var(--color-brand)]" />
                This result was already published ({d.already_published.comment.posted ? "comment posted" : "no comment"}). Post it again.
              </label>
            )}
            {error && <Banner tone="bad" title="Publishing failed">{error.message}</Banner>}
          </div>
        )}
        {result && <PublishResult result={result} />}
      </Modal>
    </>
  );
}
