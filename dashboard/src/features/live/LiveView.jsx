import { useEffect, useMemo, useState } from "react";
import { Ban, Radio, WifiOff } from "lucide-react";
import { api } from "../../lib/api.js";
import { duration } from "../../lib/format.js";
import { stageStatus } from "../../lib/runModel.js";
import { Button, Card, RunStatus, cx, useToast } from "../../ui.jsx";
import ExecutionGraph, { useNow } from "./ExecutionGraph.jsx";
import Inspector from "./Inspector.jsx";
import Timeline from "./Timeline.jsx";

function ConnectionBadge({ connection, active }) {
  if (!active) return <span className="text-xs text-faint">Stored trace</span>;
  if (connection === "reconnecting") return <span className="flex items-center gap-1.5 text-xs text-warn"><WifiOff size={13} aria-hidden /> Reconnecting…</span>;
  return <span className="flex items-center gap-1.5 text-xs text-ok"><Radio size={13} aria-hidden /> Live</span>;
}

/**
 * Live Execution: the same run as the Product View, exposed as a pipeline graph, an inspector
 * for the selected stage and the full event timeline. Everything is driven by recorded events.
 */
export default function LiveView({ pipeline, run, meta, report, connection, status, active }) {
  const toast = useToast();
  const stages = pipeline.stages;
  const [selected, setSelected] = useState(null);
  const [follow, setFollow] = useState(true);
  const now = useNow(active);

  // while the run is going, follow the newest stage that is running (else the last one finished)
  useEffect(() => {
    if (!follow || !active) return;
    const running = stages.filter((s) => stageStatus(run, s) === "running");
    const pick = running.length ? running[running.length - 1] : [...stages].reverse().find((s) => stageStatus(run, s) === "completed");
    if (pick) setSelected(pick.id);
  }, [run.lastSeq, follow, active, stages, run]);

  // a finished run opens on its decision
  useEffect(() => {
    if (!active && !selected && stages.length && run.lastSeq > 0) setSelected(run.stages.decide ? "decide" : stages.find((s) => stageStatus(run, s) !== "pending")?.id || stages[0].id);
  }, [active, selected, stages, run]);

  const def = stages.find((s) => s.id === selected);
  const upstream = useMemo(
    () => pipeline.edges.filter((e) => e.to === selected).map((e) => ({ id: e.from, label: stages.find((s) => s.id === e.from)?.label, status: stageStatus(run, stages.find((s) => s.id === e.from)) })),
    [pipeline.edges, selected, stages, run],
  );
  const pick = (id) => { setSelected(id); setFollow(false); };
  const elapsed = run.startedAt ? (run.endedAt || now) - run.startedAt : null;
  const tokens = Object.values(run.stages).reduce((n, s) => n + (s.tokens || 0), 0);

  const cancel = async () => {
    try {
      await api(`/analyses/${meta.run_id}/cancel`, { method: "POST" });
      toast.push("Cancellation requested.", "info");
    } catch (e) {
      toast.push(e.message, "error");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-line bg-s1 px-4 py-2.5 text-sm">
        <RunStatus status={status} />
        <ConnectionBadge connection={connection} active={active} />
        <span className="text-muted">Elapsed <span className="mono text-ink">{elapsed != null ? duration(elapsed) : "—"}</span></span>
        <span className="text-muted">Events <span className="mono text-ink">{run.events.length}</span></span>
        <span className="text-muted">Tokens <span className="mono text-ink">{tokens}</span></span>
        {active && <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="accent-[var(--color-brand)]" /> Follow active stage</label>}
        {active && meta && <Button size="sm" variant="danger" icon={Ban} className="ml-auto" onClick={cancel}>Cancel run</Button>}
        {meta?.imported && <span className="ml-auto text-xs text-faint">Imported from the command line; timeline reconstructed from its recorded trace</span>}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <Card title="Pipeline" className="xl:max-h-[640px] xl:overflow-y-auto">
          <ExecutionGraph stages={stages} edges={pipeline.edges} run={run} selected={selected} onSelect={pick} />
        </Card>
        <Card pad={false} className="h-[640px] overflow-hidden">
          <Inspector stage={selected} def={def} info={run.stages[selected]} status={def ? stageStatus(run, def) : "pending"} upstream={upstream} report={report} onSelect={pick} />
        </Card>
      </div>

      <Card title="Execution timeline" pad={false} className="flex h-[360px] flex-col overflow-hidden">
        <div className="min-h-0 flex-1"><Timeline events={run.events} stages={stages} selectedStage={selected} onSelectStage={pick} live={active} /></div>
      </Card>
    </div>
  );
}
