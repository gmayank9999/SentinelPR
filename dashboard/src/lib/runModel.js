// Turns the execution event stream of one run into the state the UI renders.
// The reducer is pure so a live stream, a reload and a replay of a stored log all give the same state.

export const initialRun = () => ({ events: [], lastSeq: 0, runStatus: "unknown", stages: {}, decision: null, risk: null, error: null, startedAt: null, endedAt: null });

const blank = (id) => ({
  id, status: "pending", startedAt: null, endedAt: null, duration: null, message: "", detail: null, tokens: null, model: null, retries: 0,
  error: null, reason: null, substeps: [], llmCalls: [], logs: [], reconstructed: false, eventSeqs: [],
});

export function applyEvent(state, event) {
  if (event.seq <= state.lastSeq) return state; // already applied (reconnects replay)
  const next = { ...state, events: [...state.events, event], lastSeq: event.seq, stages: { ...state.stages } };
  const d = event.data || {};
  const touch = (id, change) => {
    const prev = next.stages[id] || blank(id);
    next.stages[id] = { ...prev, ...change(prev), eventSeqs: [...prev.eventSeqs, event.seq] };
  };
  switch (event.kind) {
    case "run_queued":
      next.runStatus = "queued";
      break;
    case "run_started":
      next.runStatus = "running";
      next.startedAt = event.ts;
      break;
    case "run_completed":
      next.runStatus = "completed";
      next.decision = d.decision ?? null;
      next.risk = d.risk ?? null;
      next.endedAt = event.ts;
      break;
    case "run_failed":
      next.runStatus = "failed";
      next.error = event.message;
      next.endedAt = event.ts;
      break;
    case "run_cancelled":
      next.runStatus = "cancelled";
      next.endedAt = event.ts;
      break;
    case "stage_started":
      touch(event.stage, () => ({ status: "running", startedAt: event.ts, message: event.message }));
      break;
    case "stage_completed":
      touch(event.stage, (p) => ({
        status: "completed", startedAt: d.started_at ?? p.startedAt, endedAt: d.ended_at ?? event.ts, duration: d.duration_s ?? null,
        message: event.message, detail: d.detail ?? null, tokens: d.tokens ?? null, model: d.model ?? null, retries: d.retries ?? 0,
        reconstructed: Boolean(d.reconstructed), cached: d.cached,
      }));
      break;
    case "stage_failed":
      touch(event.stage, (p) => ({ status: "failed", endedAt: d.ended_at ?? event.ts, duration: d.duration_s ?? null, error: d.error || event.message, message: event.message, startedAt: d.started_at ?? p.startedAt }));
      break;
    case "stage_skipped":
      touch(event.stage, () => ({ status: "skipped", reason: d.reason || event.message, reconstructed: Boolean(d.reconstructed) }));
      break;
    case "substep":
      touch(event.stage, (p) => ({ substeps: [...p.substeps, event] }));
      break;
    case "llm_call":
      touch(event.stage, (p) => ({ llmCalls: [...p.llmCalls, event] }));
      break;
    case "log":
      touch(event.stage, (p) => ({ logs: [...p.logs, event] }));
      break;
    default:
      break;
  }
  return next;
}

export const replay = (events, from = initialRun()) => events.reduce(applyEvent, from);

export const TERMINAL = ["completed", "failed", "cancelled"];
export const isActive = (status) => status === "queued" || status === "running";

/** Display status of a stage. Before the run starts the first stage waits; later ones are pending. */
export function stageStatus(run, stage) {
  const s = run.stages[stage.id];
  if (s) return s.status;
  if (run.runStatus === "queued") return stage.id === "intake" ? "waiting" : "pending";
  if (TERMINAL.includes(run.runStatus)) return stage.group === "post" ? "skipped" : "pending"; // never started: telemetry unavailable
  return "pending";
}

/** Group the verifier's sub-steps: tests, coverage, mutation (with its mutants). */
export function verifySteps(stage) {
  const steps = { tests: null, coverage: null, mutation: null, mutants: [] };
  for (const e of stage?.substeps || []) {
    const d = e.data || {};
    if (d.step === "mutant") steps.mutants.push(d);
    else if (d.step && d.step in steps) steps[d.step] = { status: e.status, message: e.message, ...d };
  }
  return steps;
}

/** Layers of the stage graph for drawing: longest distance from a source, parallel stages share a layer. */
export function layers(stages, edges) {
  const ids = stages.map((s) => s.id);
  const parents = Object.fromEntries(ids.map((id) => [id, []]));
  edges.forEach((e) => parents[e.to]?.push(e.from));
  const depth = {};
  const visit = (id) => {
    if (depth[id] != null) return depth[id];
    depth[id] = parents[id].length ? 1 + Math.max(...parents[id].map(visit)) : 0;
    return depth[id];
  };
  ids.forEach(visit);
  const rows = [];
  stages.forEach((s) => (rows[depth[s.id]] = rows[depth[s.id]] || []).push(s));
  return rows.filter(Boolean);
}
