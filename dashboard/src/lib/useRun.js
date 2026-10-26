import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, streamUrl } from "./api.js";
import { applyEvent, initialRun, isActive } from "./runModel.js";

const reducer = (state, action) => {
  if (action.type === "reset") return initialRun();
  if (action.type === "events") return action.events.reduce(applyEvent, state);
  return state;
};

/**
 * One analysis run: its meta, its report once complete, and its live event state.
 * Loads the stored log first, then follows the SSE stream for runs that are still going,
 * resuming from the last event after a dropped connection.
 */
export function useRun(runId) {
  const [run, dispatch] = useReducer(reducer, undefined, initialRun);
  const [detail, setDetail] = useState({ meta: null, report: null, error: null, loading: Boolean(runId) });
  const [connection, setConnection] = useState("idle"); // idle | live | reconnecting | closed
  const source = useRef(null);
  const lastSeq = useRef(0);
  lastSeq.current = run.lastSeq;

  const loadDetail = useCallback(async () => {
    try {
      const data = await api(`/analyses/${encodeURIComponent(runId)}`);
      setDetail({ meta: data.meta, report: data.report, error: null, loading: false });
      return data.meta;
    } catch (error) {
      setDetail({ meta: null, report: null, error, loading: false });
      return null;
    }
  }, [runId]);

  useEffect(() => {
    if (!runId) return undefined;
    let cancelled = false;
    dispatch({ type: "reset" });
    setDetail({ meta: null, report: null, error: null, loading: true });
    setConnection("idle");

    (async () => {
      const meta = await loadDetail();
      if (cancelled || !meta) return;
      let from = 0; // the stream resumes after the last event already loaded
      try {
        const stored = await api(`/analyses/${encodeURIComponent(runId)}/events?after=0`);
        if (cancelled) return;
        dispatch({ type: "events", events: stored.events });
        from = stored.events.length ? stored.events[stored.events.length - 1].seq : 0;
        if (stored.closed) {
          setConnection("closed");
          return;
        }
      } catch {
        /* fall through to the stream */
      }
      const open = () => {
        const es = new EventSource(streamUrl(runId, Math.max(from, lastSeq.current)));
        source.current = es;
        es.onopen = () => !cancelled && setConnection("live");
        es.onmessage = (m) => {
          const event = JSON.parse(m.data);
          dispatch({ type: "events", events: [event] });
          if (["run_completed", "run_failed", "run_cancelled"].includes(event.kind)) loadDetail();
        };
        es.addEventListener("end", () => {
          es.close();
          if (!cancelled) {
            setConnection("closed");
            loadDetail();
          }
        });
        es.onerror = () => {
          if (!cancelled) setConnection("reconnecting"); // EventSource retries and resumes with Last-Event-ID
        };
      };
      open();
    })();

    return () => {
      cancelled = true;
      source.current?.close();
    };
  }, [runId, loadDetail]);

  const status = run.runStatus !== "unknown" ? run.runStatus : detail.meta?.status || "unknown";
  return { run, meta: detail.meta, report: detail.report, error: detail.error, loading: detail.loading, connection, status, active: isActive(status), reload: loadDetail };
}
