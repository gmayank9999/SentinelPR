import { CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import { useApi } from "../../lib/api.js";
import { duration } from "../../lib/format.js";
import { Async, Card, PageHeader, Stat } from "../../ui.jsx";

export default function Health() {
  const status = useApi("/service/status", { interval: 5000 });
  return (
    <div>
      <PageHeader title="System Health" subtitle="What this SentinelPR server can and cannot do right now." />
      <Async state={status}>
        {(s) => (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Stat label="Version" value={s.version} hint={`Python ${s.python} · ${s.platform}`} />
              <Stat label="Uptime" value={duration(s.uptime_s)} />
              <Stat label="Queue" value={`${s.queue.running} running`} hint={`${s.queue.queued} queued · ${s.queue.max_concurrent} at once`} />
              <Stat label="Analyses stored" value={s.runs} hint={`${s.repositories} repositories`} />
              <Stat label="Disk free" value={`${s.storage.free_gb} GB`} hint={s.storage.workdir} />
            </div>
            <Card title="Checks" pad={false}>
              <ul className="divide-y divide-line">
                {s.checks.map((c) => (
                  <li key={c.id} className="flex gap-3 px-5 py-3.5">
                    {c.ok ? <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-ok" aria-label="ok" /> : c.id === "sandbox" || c.id === "auth" ? <XCircle size={17} className="mt-0.5 shrink-0 text-warn" aria-label="limitation" /> : <MinusCircle size={17} className="mt-0.5 shrink-0 text-faint" aria-label="not configured" />}
                    <div><div className="text-sm font-medium">{c.label}</div><div className="text-sm text-muted">{c.detail}</div></div>
                  </li>))}
              </ul>
            </Card>
            <Card title="Language models">
              <ul className="space-y-1.5 text-sm">{Object.entries(s.llm).map(([name, p]) => <li key={name} className="flex justify-between"><span><b className="font-medium">{name}</b> <span className="mono text-faint">{p.model}</span></span><span className={p.available ? "text-ok" : "text-faint"}>{p.available ? "available" : "not configured"}</span></li>)}</ul>
            </Card>
          </div>
        )}
      </Async>
    </div>
  );
}
