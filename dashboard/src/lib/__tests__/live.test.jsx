import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExecutionGraph from "../../features/live/ExecutionGraph.jsx";
import Timeline from "../../features/live/Timeline.jsx";
import { useAnalyze } from "../analyze.jsx";
import { useRun } from "../useRun.js";
import { initialRun, replay } from "../runModel.js";
import { ToastProvider } from "../../ui.jsx";
import pipeline from "./pipeline.fixture.json";

let seq = 0;
const ev = (stage, kind, status = "info", data = {}, message = "") => ({ seq: ++seq, ts: 100 + seq, stage, kind, status, message, data });

class FakeEventSource {
  static last = null;
  constructor(url) { this.url = url; this.listeners = {}; FakeEventSource.last = this; setTimeout(() => this.onopen?.(), 0); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; }
  push(event) { this.onmessage?.({ data: JSON.stringify(event) }); }
  end() { this.listeners.end?.(); }
}

const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

beforeEach(() => { seq = 0; vi.stubGlobal("EventSource", FakeEventSource); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Probe({ id }) {
  const { run, status, connection, report } = useRun(id);
  return (
    <div>
      <span data-testid="status">{status}</span><span data-testid="conn">{connection}</span><span data-testid="count">{run.events.length}</span>
      <span data-testid="decision">{report?.decision || ""}</span><span data-testid="verify">{run.stages.verify?.status || "none"}</span>
    </div>
  );
}

describe("useRun", () => {
  it("loads the stored events, then follows the live stream from where it stopped", async () => {
    const stored = [ev("run", "run_queued", "queued"), ev("run", "run_started", "running"), ev("verify", "stage_started", "running")];
    let detail = { meta: { run_id: "r1", status: "running" }, report: null };
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (url.includes("/events")) return json({ events: stored, closed: false });
      return json(detail);
    }));
    render(<Probe id="r1" />);
    await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("3"));
    expect(screen.getByTestId("verify").textContent).toBe("running");
    await waitFor(() => expect(FakeEventSource.last).toBeTruthy());
    expect(FakeEventSource.last.url).toContain("after=3"); // resumes after the last stored event

    act(() => FakeEventSource.last.push(ev("verify", "stage_completed", "completed", { duration_s: 2 })));
    expect(screen.getByTestId("verify").textContent).toBe("completed");

    detail = { meta: { run_id: "r1", status: "completed", decision: "BLOCK" }, report: { decision: "BLOCK" } };
    act(() => FakeEventSource.last.push(ev("run", "run_completed", "completed", { decision: "BLOCK" })));
    await waitFor(() => expect(screen.getByTestId("decision").textContent).toBe("BLOCK"));
    expect(screen.getByTestId("status").textContent).toBe("completed");
    act(() => FakeEventSource.last.end());
    expect(FakeEventSource.last.closed).toBe(true);
  });

  it("does not open a stream for a finished run", async () => {
    FakeEventSource.last = null;
    vi.stubGlobal("fetch", vi.fn((url) => (url.includes("/events")
      ? json({ events: [ev("run", "run_completed", "completed", { decision: "PASS" })], closed: true })
      : json({ meta: { run_id: "r2", status: "completed", decision: "PASS" }, report: { decision: "PASS" } }))));
    render(<Probe id="r2" />);
    await waitFor(() => expect(screen.getByTestId("conn").textContent).toBe("closed"));
    expect(FakeEventSource.last).toBeNull();
  });

  it("reports an unknown run instead of hanging", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ error: { code: "not_found", message: "Unknown run." } }, 404)));
    render(<Probe id="nope" />);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unknown"));
  });
});

describe("ExecutionGraph", () => {
  const run = replay([
    ev("run", "run_started", "running"), ev("parse_diff", "stage_completed", "completed", { duration_s: 0.05 }),
    ev("impact", "stage_started", "running"), ev("historian", "stage_completed", "completed", { duration_s: 0.2 }), ev("signals", "stage_started", "running"),
  ], initialRun());

  it("shows each parallel agent in its own state, with a word as well as an icon", () => {
    render(<ExecutionGraph stages={pipeline.stages} edges={pipeline.edges} run={run} selected={null} onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Impact agent: Running" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Historian agent: Completed" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Signals agent: Running" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verification: Pending" })).toBeTruthy();
  });

  it("selects a stage on click and marks it pressed", () => {
    const onSelect = vi.fn();
    render(<ExecutionGraph stages={pipeline.stages} edges={pipeline.edges} run={run} selected="impact" onSelect={onSelect} />);
    expect(screen.getByRole("button", { name: "Impact agent: Running" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Historian agent: Completed" }));
    expect(onSelect).toHaveBeenCalledWith("historian");
  });
});

describe("Timeline", () => {
  const events = [
    ev("guard_in", "stage_completed", "completed", { duration_s: 0.1 }, "guard_in finished"),
    ev("verify", "substep", "info", { step: "mutant", status: "survived" }, "mutant survived"),
    ev("verify", "stage_failed", "failed", { error: "x" }, "verify crashed"),
  ];

  it("filters by event type and by stage", () => {
    render(<Timeline events={events} stages={pipeline.stages} selectedStage={null} onSelectStage={() => {}} live={false} />);
    expect(screen.getByText("3 of 3")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter by event type"), { target: { value: "problems" } });
    expect(screen.getByText("1 of 3")).toBeTruthy();
    expect(screen.getByText("verify crashed")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter by event type"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Filter by stage"), { target: { value: "guard_in" } });
    expect(screen.getByText("1 of 3")).toBeTruthy();
  });

  it("jumps to the stage an event belongs to", () => {
    const onSelectStage = vi.fn();
    render(<Timeline events={events} stages={pipeline.stages} selectedStage={null} onSelectStage={onSelectStage} live={false} />);
    fireEvent.click(screen.getAllByRole("button", { name: "verify" })[0]);
    expect(onSelectStage).toHaveBeenCalledWith("verify");
  });
});

describe("analysing an untrusted repository", () => {
  function Starter() {
    const { start, dialog } = useAnalyze();
    return <div><button onClick={() => start({ id: "o--r", full_name: "o/r" }, "7")}>go</button>{dialog}</div>;
  }
  const mount = () => render(<MemoryRouter><ToastProvider><Starter /></ToastProvider></MemoryRouter>);

  it("asks for trust first, then trusts and starts only after an explicit confirmation", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts) => {
      calls.push(`${opts?.method || "GET"} ${url.split("/api")[1]}`);
      if (url.endsWith("/analyses") && calls.filter((c) => c.endsWith("/analyses")).length === 1) return json({ error: { code: "not_trusted", message: "not trusted" } }, 403);
      if (url.endsWith("/trust")) return json({ trusted: true });
      return json({ run_id: "run-9" }, 202);
    }));
    mount();
    fireEvent.click(screen.getByText("go"));
    await screen.findByRole("dialog");
    const confirm = screen.getByRole("button", { name: "Trust and analyse" });
    expect(confirm.disabled).toBe(true); // nothing happens until the box is ticked
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() => expect(calls).toContain("POST /analyses"));
    expect(calls).toEqual(["POST /analyses", "POST /repos/o--r/trust", "POST /analyses"]);
  });
});
