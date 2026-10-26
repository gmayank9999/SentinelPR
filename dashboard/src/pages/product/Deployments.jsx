import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Ban, CheckCircle2, CircleDashed, FlaskConical, History, Pause, Play, Plus, Rocket, Undo2, XCircle } from "lucide-react";
import { api, useApi } from "../../lib/api.js";
import { ago, duration, pct, short, stamp } from "../../lib/format.js";
import { Async, Banner, Button, Card, EmptyState, ErrorState, Modal, PageHeader, SkeletonRows, cx, useToast } from "../../ui.jsx";

const STATUS = {
  queued: ["Queued", "text-muted"], provisioning: ["Provisioning", "text-brand"], rolling_out: ["Rolling out", "text-brand"], paused: ["Paused", "text-warn"],
  promoted: ["Promoted in staging", "text-ok"], rolled_back: ["Rolled back", "text-warn"], failed: ["Failed", "text-bad"],
};
const ACTIVE = ["queued", "provisioning", "rolling_out", "paused"];
const Status = ({ status }) => { const [label, tone] = STATUS[status] || [status, "text-muted"]; return <span className={cx("text-xs font-medium", tone)}>{label}</span>; };

function Verified({ label, value }) {
  const [Icon, tone, text] = value === true ? [CheckCircle2, "text-ok", "verified"] : value === false ? [XCircle, "text-bad", "NOT verified"] : [CircleDashed, "text-faint", "not applicable yet"];
  return <div className="flex items-center gap-2 text-sm"><Icon size={15} className={tone} aria-hidden />{label}<span className={cx("text-xs", tone)}>{text}</span></div>;
}

function NewDeployment({ open, onClose, onCreated }) {
  const toast = useToast();
  const targets = useApi(open ? "/deployments/targets" : null);
  const branches = useApi(open ? "/repos/unierp-demo/pulls" : null);
  const [form, setForm] = useState({ target: "local-processes", stable_ref: "main", candidate_ref: "", step_seconds: 10, max_error_rate: 0.01, p95_ratio: 1.5, fault: "none", rate: 0.5, confirm: false });
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open && !form.candidate_ref && branches.data?.[0]) setForm((f) => ({ ...f, candidate_ref: branches.data[0].head_ref })); }, [open, branches.data, form.candidate_ref]);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const target = targets.data?.find((t) => t.name === form.target);
  const create = async () => {
    setBusy(true);
    try {
      const body = { target: form.target, stable_ref: form.stable_ref, candidate_ref: form.candidate_ref, step_seconds: Number(form.step_seconds), max_error_rate: Number(form.max_error_rate), p95_ratio: Number(form.p95_ratio), confirm: form.confirm,
        test_fault: form.fault === "none" ? null : { kind: form.fault, rate: Number(form.rate) } };
      const created = await api("/deployments", { method: "POST", body });
      toast.push("Deployment started.", "success");
      onCreated(created.id);
      onClose();
    } catch (e) {
      toast.push(e.message, "error");
    } finally { setBusy(false); }
  };
  const field = "mt-1 h-9 w-full rounded-md border border-line2 bg-bg px-2 text-sm outline-none focus:border-brand";
  return (
    <Modal open={open} onClose={onClose} title="New staging deployment" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="primary" icon={Rocket} loading={busy} disabled={!form.confirm || !form.candidate_ref || (target && !target.ready)} onClick={create}>Start rollout</Button></>}>
      <Banner title="Staging only">SentinelPR never deploys to production. The rollout shifts 10% → 25% → 50% → 100% of synthetic traffic, judges each step against the SLOs, and rolls back automatically on a breach.</Banner>
      <label className="block text-sm">Deployment target
        <select className={field} value={form.target} onChange={set("target")}>{(targets.data || []).map((t) => <option key={t.name} value={t.name}>{t.name} ({t.kind}, {t.environment}){t.ready ? "" : " — not ready"}</option>)}</select></label>
      {target && !target.ready && <p className="text-sm text-bad">{target.note}</p>}
      {target && !target.capabilities.weighted_routing && <p className="text-sm text-bad">{target.capabilities.note}</p>}
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-sm">Baseline (stable)<input className={field} value={form.stable_ref} onChange={set("stable_ref")} /></label>
        <label className="block text-sm">Candidate commit
          <input list="candidates" className={field} value={form.candidate_ref} onChange={set("candidate_ref")} /><datalist id="candidates">{(branches.data || []).map((b) => <option key={b.head_ref} value={b.head_ref}>{b.title}</option>)}</datalist></label>
        <label className="block text-sm">Seconds per step<input type="number" min="3" className={field} value={form.step_seconds} onChange={set("step_seconds")} /></label>
        <label className="block text-sm">Max error rate<input type="number" step="0.005" min="0" className={field} value={form.max_error_rate} onChange={set("max_error_rate")} /></label>
      </div>
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-xs text-faint"><FlaskConical size={12} className="mr-1 inline" aria-hidden />Validation only: inject a fault into the candidate</legend>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">Fault<select className={field} value={form.fault} onChange={set("fault")}><option value="none">none (a real rollout)</option><option value="errors">HTTP 500 errors</option><option value="slow">added latency</option></select></label>
          <label className="block text-sm">Share of requests affected<input type="number" step="0.1" min="0" max="1" className={field} value={form.rate} onChange={set("rate")} disabled={form.fault === "none"} /></label>
        </div>
        {form.fault !== "none" && <p className="mt-2 text-xs text-warn">The deployment will be labelled as having a deliberately injected fault, so its rollback is not mistaken for a real regression.</p>}
      </fieldset>
      <label className="flex cursor-pointer items-start gap-2.5 text-muted"><input type="checkbox" checked={form.confirm} onChange={set("confirm")} className="mt-0.5 h-4 w-4 accent-[var(--color-brand)]" />
        I confirm this deploys the candidate to the selected <b className="text-ink">{target?.environment || "staging"}</b> target, not to production.</label>
    </Modal>
  );
}

function Detail({ id }) {
  const toast = useToast();
  const dep = useApi(`/deployments/${id}`, { interval: 1500 });
  if (dep.loading) return <SkeletonRows rows={3} />;
  if (dep.error) return <ErrorState error={dep.error} onRetry={dep.refetch} />;
  const { state: s, audit } = dep.data;
  const active = ACTIVE.includes(s.status);
  const act = async (action) => { try { await api(`/deployments/${id}/${action}`, { method: "POST" }); dep.reload(); } catch (e) { toast.push(e.message, "error"); } };
  const last = s.measurements[s.measurements.length - 1];
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-3"><h2 className="text-base font-semibold">{short(s.candidate.sha)} <span className="text-faint">against</span> {short(s.stable.sha)}</h2><Status status={s.status} /></div>
            <div className="mt-1 text-xs text-faint">{s.target} · {s.kind} · {s.environment} · started {ago(s.created_at)}{s.ended_at ? ` · ${duration(s.ended_at - s.started_at)}` : ""}</div>
          </div>
          <div className="flex gap-2">
            {s.status === "paused" ? <Button size="sm" icon={Play} onClick={() => act("resume")}>Resume</Button> : <Button size="sm" icon={Pause} disabled={!active} onClick={() => act("pause")}>Pause</Button>}
            <Button size="sm" variant="danger" icon={Ban} disabled={!active} onClick={() => act("abort")}>Abort</Button>
            <Button size="sm" variant="danger" icon={Undo2} disabled={!active} onClick={() => act("rollback")}>Roll back</Button>
          </div>
        </div>
        {s.test_fault && <div className="mt-3"><Banner tone="warn" title="A fault was injected on purpose">This deployment ran with an injected {s.test_fault.kind} fault ({pct(s.test_fault.rate)} of candidate requests) to validate the rollback. It is not a real regression.</Banner></div>}
        {s.reason && <p className={cx("mt-3 text-sm", s.status === "failed" ? "text-bad" : "text-muted")}>{s.reason}</p>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Traffic allocation">
          <div className="space-y-3 text-sm">
            <div className="flex justify-between"><span className="text-muted">Weight requested</span><b className="mono">{pct(s.weight_requested)}</b></div>
            <div className="flex justify-between"><span className="text-muted">Weight read back from the router</span><b className="mono">{s.weight_observed == null ? "not measured" : pct(s.weight_observed)}</b></div>
            <div className="flex justify-between"><span className="text-muted">Candidate share actually served (latest window)</span><b className="mono">{last ? pct(last.candidate_share, 1) : "not measured"}</b></div>
            <div className="h-2.5 overflow-hidden rounded-full bg-s3"><div className="h-full bg-brand" style={{ width: `${(last?.candidate_share || 0) * 100}%` }} /></div>
            <p className="text-xs text-faint">Shares come from the applications' own request counters, not from the router's claim.</p>
          </div>
        </Card>
        <Card title="What has been proven">
          <div className="space-y-2.5">
            <Verified label="Traffic reached the candidate as configured" value={s.step_results.length ? s.step_results.every((r) => r.routing_verified) : null} />
            <Verified label="Rollback restored the baseline" value={s.rollback_verified} />
            <Verified label="Candidate serves 100% after promotion" value={s.promotion_verified} />
          </div>
          <p className="mt-3 text-xs text-faint">Verification is by observation: probe requests are sent and the version header each application puts on its response is counted.</p>
        </Card>
      </div>

      <Card title="Rollout steps and SLOs" pad={false}>
        <div className="overflow-x-auto"><table className="table"><thead><tr><th>Step</th><th className="text-right">Candidate served</th><th className="text-right">Requests (cand / stable)</th><th className="text-right">Candidate errors</th><th className="text-right">Candidate p95</th><th>Result</th></tr></thead><tbody>
          {s.steps.map((w) => { const r = s.step_results.find((x) => x.weight === w); const running = !r && s.current_step === w && active;
            return (<tr key={w}><td className="mono">{pct(w)}</td>{r ? (<><td className="mono text-right">{pct(r.candidate_share, 1)}</td><td className="mono text-right">{r.candidate.requests} / {r.stable.requests}</td>
              <td className="mono text-right">{pct(r.candidate.error_rate, 1)}</td><td className="mono text-right">{Math.round(r.candidate.p95_ms)} ms</td>
              <td className={r.healthy ? "text-ok" : "text-bad"}>{r.healthy ? "healthy" : (r.breaches[0] || "routing mismatch")}</td></>) : <td colSpan={5} className="text-faint">{running ? "measuring…" : "not reached"}</td>}</tr>); })}
        </tbody></table></div>
        <p className="px-5 py-3 text-xs text-faint">SLO: error rate ≤ {pct(s.slo.max_error_rate, 1)} and no more than {s.slo.max_error_rate_delta * 100}% above baseline; p95 ≤ {s.slo.p95_ratio}× baseline + {s.slo.p95_slack_ms} ms; at least {s.slo.min_requests} candidate requests to judge a step.</p>
      </Card>

      <Card title={`Audit trail (${audit.length})`} pad={false}>
        <ol className="max-h-72 overflow-y-auto divide-y divide-line font-mono text-xs">
          {audit.map((e) => <li key={e.seq} className="flex gap-3 px-5 py-1.5"><span className="w-14 shrink-0 text-faint">{new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
            <span className={cx("w-28 shrink-0", e.data?.actor === "operator" ? "text-brand" : "text-faint")}>{e.data?.actor || "controller"}</span><span className="w-28 shrink-0 text-muted">{e.kind}</span><span className="font-sans text-[12.5px] text-ink">{e.message}</span></li>)}
        </ol>
      </Card>
    </div>
  );
}

export default function Deployments() {
  const list = useApi("/deployments", { interval: 4000 });
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  useEffect(() => { if (!selected && list.data?.length) setSelected(list.data[0].id); }, [list.data, selected]);
  return (
    <div>
      <PageHeader title="Canary Deployments" subtitle="Roll a commit out to a disposable staging environment in steps, measure it against SLOs, and roll back automatically. Every claim here is measured, never assumed."
        actions={<><Link to="/canary/recorded"><Button icon={History}>Recorded demo reports</Button></Link><Button variant="primary" icon={Plus} onClick={() => setOpen(true)}>New deployment</Button></>} />
      <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Card title="Deployments" pad={false}>
          <Async state={list} skeleton={<div className="p-4"><SkeletonRows rows={3} /></div>}
            empty={<div className="p-4"><EmptyState icon={Rocket} title="No deployments yet" action={<Button variant="primary" icon={Plus} onClick={() => setOpen(true)}>New deployment</Button>}>Start a rollout of a commit to a staging target.</EmptyState></div>}>
            {(items) => (
              <ul className="divide-y divide-line">
                {items.map((d) => (
                  <li key={d.id}><button type="button" onClick={() => setSelected(d.id)} aria-pressed={selected === d.id} className={cx("w-full px-4 py-3 text-left hover:bg-s2", selected === d.id && "bg-s2")}>
                    <div className="flex items-center justify-between"><span className="mono text-sm">{short(d.candidate.sha)} → {d.target}</span><Status status={d.status} /></div>
                    <div className="mt-0.5 text-xs text-faint">{stamp(d.created_at)}{d.test_fault ? " · test fault" : ""}</div></button></li>))}
              </ul>
            )}
          </Async>
        </Card>
        <div>{selected ? <Detail key={selected} id={selected} /> : <EmptyState icon={Rocket} title="Select or start a deployment" />}</div>
      </div>
      <NewDeployment open={open} onClose={() => setOpen(false)} onCreated={(id) => { setSelected(id); list.reload(); }} />
    </div>
  );
}
