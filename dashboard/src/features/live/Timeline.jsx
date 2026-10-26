import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Pause, Play } from "lucide-react";
import { clock } from "../../lib/format.js";
import { Json, cx } from "../../ui.jsx";

const TONE = { failed: "text-bad", warn: "text-warn", skipped: "text-faint", cancelled: "text-faint", queued: "text-muted", info: "text-muted" };
const KINDS = [["all", "All events"], ["stage", "Stage changes"], ["substep", "Sub-steps"], ["llm_call", "LLM calls"], ["problems", "Problems"]];

const matchesKind = (e, kind) => {
  if (kind === "all") return true;
  if (kind === "stage") return e.kind.startsWith("stage_") || e.kind.startsWith("run_");
  if (kind === "problems") return e.status === "failed" || e.status === "warn" || e.kind === "stage_failed" || e.kind === "run_failed";
  return e.kind === kind;
};

/** Chronological event log: auto-follows new events, filters, expandable structured details. */
export default function Timeline({ events, stages, selectedStage, onSelectStage, live }) {
  const [follow, setFollow] = useState(Boolean(live));
  const [stage, setStage] = useState("all");
  const [kind, setKind] = useState("all");
  const [open, setOpen] = useState({});
  const box = useRef(null);
  const rows = useMemo(() => events.filter((e) => (stage === "all" || e.stage === stage) && matchesKind(e, kind)), [events, stage, kind]);
  const t0 = events[0]?.ts;

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [rows.length, follow]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <select aria-label="Filter by stage" value={stage} onChange={(e) => setStage(e.target.value)} className="h-7 rounded border border-line2 bg-bg px-2 text-xs">
          <option value="all">All stages</option>
          <option value="run">Run</option>
          {stages.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <select aria-label="Filter by event type" value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 rounded border border-line2 bg-bg px-2 text-xs">
          {KINDS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <span className="text-xs text-faint">{rows.length} of {events.length}</span>
        {live ? (
          <button type="button" onClick={() => setFollow(!follow)} aria-pressed={follow}
            className="ml-auto flex items-center gap-1.5 rounded border border-line2 px-2 py-1 text-xs text-muted hover:text-ink">
            {follow ? <Pause size={12} aria-hidden /> : <Play size={12} aria-hidden />} {follow ? "Following new events" : "Paused: click to follow"}
          </button>
        ) : <span className="ml-auto text-xs text-faint">Complete trace</span>}
      </div>
      <ol ref={box} onScroll={(e) => { const el = e.currentTarget; if (live && el.scrollHeight - el.scrollTop - el.clientHeight > 60 && follow) setFollow(false); }}
        className="min-h-0 flex-1 overflow-y-auto font-mono text-[12px]" aria-label="Execution timeline">
        {rows.length === 0 && <li className="p-4 text-sm text-muted">No events match.</li>}
        {rows.map((e) => {
          const has = e.data && Object.keys(e.data).length > 0;
          const isOpen = open[e.seq];
          return (
            <li key={e.seq} className={cx("border-b border-line/50", selectedStage === e.stage && "bg-s2")}>
              <div className="flex items-start gap-2 px-3 py-1.5">
                <button type="button" aria-label={has ? "Toggle details" : "No details"} disabled={!has} aria-expanded={isOpen} onClick={() => setOpen({ ...open, [e.seq]: !isOpen })}
                  className="mt-0.5 text-faint disabled:opacity-20">{isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
                <span className="w-16 shrink-0 text-faint" title={t0 ? `+${(e.ts - t0).toFixed(2)}s` : ""}>{clock(e.ts)}</span>
                <button type="button" onClick={() => e.stage !== "run" && onSelectStage(e.stage)} className="w-28 shrink-0 truncate text-left text-brand hover:underline" title={e.stage}>{e.stage}</button>
                <span className="w-28 shrink-0 truncate text-faint">{e.kind}</span>
                <span className={cx("min-w-0 flex-1 break-words font-sans text-[12.5px]", TONE[e.status] || "text-ink")}>{e.message}</span>
              </div>
              {isOpen && has && <div className="px-9 pb-2"><Json value={e.data} label="Structured details" /></div>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
