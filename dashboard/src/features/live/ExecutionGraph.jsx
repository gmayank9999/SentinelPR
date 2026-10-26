import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { duration } from "../../lib/format.js";
import { layers, stageStatus } from "../../lib/runModel.js";
import { STAGE, StageIcon, cx } from "../../ui.jsx";

const MAX_NODE_W = 200;
const NODE_H = 56;
const GAP_X = 14;
const GAP_Y = 34;

/** Width of an element, kept up to date (the graph fits its pane instead of scrolling). */
function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useLayoutEffect(() => {
    if (!ref.current) return undefined;
    setWidth(Math.floor(ref.current.getBoundingClientRect().width) || 640);
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Ticks while something is running so elapsed times move; stops when nothing is. */
export function useNow(active) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now() / 1000), 250);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function geometry(stages, edges, available) {
  const rows = layers(stages, edges);
  const widest = Math.max(...rows.map((r) => r.length));
  const NODE_W = Math.max(112, Math.min(MAX_NODE_W, Math.floor((available - (widest - 1) * GAP_X) / widest)));
  const width = widest * NODE_W + (widest - 1) * GAP_X;
  const pos = {};
  rows.forEach((row, i) => {
    const rowW = row.length * NODE_W + (row.length - 1) * GAP_X;
    row.forEach((s, k) => (pos[s.id] = { x: (width - rowW) / 2 + k * (NODE_W + GAP_X), y: i * (NODE_H + GAP_Y) }));
  });
  return { pos, width, height: rows.length * (NODE_H + GAP_Y) - GAP_Y, nodeW: NODE_W };
}

function edgeStyle(from, to) {
  if (from === "skipped" || to === "skipped") return { stroke: "var(--color-line2)", dash: "3 4", width: 1 };
  if (to === "running" && from === "completed") return { stroke: "var(--color-brand)", dash: "", width: 2 };
  if (from === "completed" && (to === "completed" || to === "failed")) return { stroke: "var(--color-faint)", dash: "", width: 1.5 };
  if (to === "failed" || from === "failed") return { stroke: "var(--color-bad)", dash: "", width: 1.5 };
  return { stroke: "var(--color-line2)", dash: "5 4", width: 1 };
}

/**
 * The pipeline as a vertical flow. Stages that run in parallel share a row and update on their own.
 * Nodes are real buttons laid over an SVG of the edges, so keyboard and screen readers work.
 */
export default function ExecutionGraph({ stages, edges, run, selected, onSelect }) {
  const [box, available] = useWidth();
  const g = useMemo(() => geometry(stages, edges, available), [stages, edges, available]);
  const NODE_W = g.nodeW;
  const statusOf = Object.fromEntries(stages.map((s) => [s.id, stageStatus(run, s)]));
  const running = Object.values(statusOf).includes("running");
  const now = useNow(running);
  const nodes = useRef({});

  // keep the selected stage in view while the run advances through a tall graph
  useEffect(() => {
    nodes.current[selected]?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  return (
    <div ref={box} className="pb-2">
      <div className="relative mx-auto" style={{ width: g.width, height: g.height }}>
        <svg className="absolute inset-0" width={g.width} height={g.height} aria-hidden>
          {edges.map((e) => {
            const a = g.pos[e.from], b = g.pos[e.to];
            if (!a || !b) return null;
            const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y;
            const mid = (y1 + y2) / 2;
            const st = edgeStyle(statusOf[e.from], statusOf[e.to]);
            return <path key={`${e.from}-${e.to}`} d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`} fill="none" stroke={st.stroke} strokeWidth={st.width} strokeDasharray={st.dash} />;
          })}
        </svg>
        {stages.map((s) => {
          const st = statusOf[s.id];
          const info = run.stages[s.id];
          const isSel = selected === s.id;
          const elapsed = st === "running" && info?.startedAt ? now - info.startedAt : null;
          const sub = st === "skipped" ? "skipped" : st === "failed" ? "failed" : st === "running" ? `running · ${duration(elapsed)}`
            : st === "completed" ? (info?.cached ? "cached" : duration(info?.duration)) : STAGE[st].label.toLowerCase();
          return (
            <button
              key={s.id} ref={(el) => { nodes.current[s.id] = el; }} type="button" onClick={() => onSelect(s.id)} aria-pressed={isSel} aria-label={`${s.label}: ${STAGE[st].label}`}
              style={{ left: g.pos[s.id].x, top: g.pos[s.id].y, width: NODE_W, height: NODE_H }}
              className={cx("absolute flex items-center gap-2.5 rounded-lg border bg-s2 px-3 text-left transition",
                isSel ? "border-brand ring-1 ring-brand" : "border-line hover:border-line2",
                st === "running" && "node-active border-brand/60", st === "skipped" && "border-dashed opacity-60", st === "pending" && "opacity-75",
                st === "failed" && "border-bad/50")}
            >
              <StageIcon status={st} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-ink">{s.label}</span>
                <span className="block truncate text-[11px] text-muted">{sub}</span>
              </span>
              {s.group === "pipeline" && info?.tokens > 0 && <span className="mono text-[10px] text-faint">{info.tokens}t</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
