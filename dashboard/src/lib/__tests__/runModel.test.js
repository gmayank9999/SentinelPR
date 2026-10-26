import { describe, expect, it } from "vitest";
import { applyEvent, initialRun, layers, replay, stageStatus, verifySteps } from "../runModel.js";
import pipeline from "./pipeline.fixture.json";

let seq = 0;
const ev = (stage, kind, status = "info", data = {}, message = "") => ({ seq: ++seq, ts: 100 + seq, stage, kind, status, message, data });
const stage = (id) => pipeline.stages.find((s) => s.id === id);

describe("run state from events", () => {
  it("tracks three agents running in parallel independently", () => {
    seq = 0;
    let run = initialRun();
    run = [ev("run", "run_queued"), ev("run", "run_started"), ev("parse_diff", "stage_completed", "completed", { duration_s: 0.1 })].reduce(applyEvent, run);
    run = [ev("impact", "stage_started"), ev("historian", "stage_started"), ev("signals", "stage_started")].reduce(applyEvent, run);
    expect(["impact", "historian", "signals"].map((id) => stageStatus(run, stage(id)))).toEqual(["running", "running", "running"]);
    run = applyEvent(run, ev("signals", "stage_completed", "completed", { duration_s: 0.4, detail: { intent: "refactor" } }));
    expect(stageStatus(run, stage("signals"))).toBe("completed");
    expect(stageStatus(run, stage("impact"))).toBe("running");
    expect(run.stages.signals.detail.intent).toBe("refactor");
  });

  it("ignores events it has already applied (a reconnect replays them)", () => {
    seq = 0;
    const events = [ev("run", "run_started"), ev("guard_in", "stage_started"), ev("guard_in", "stage_completed", "completed")];
    const once = replay(events);
    const twice = replay(events, once);
    expect(twice).toBe(once);
    expect(twice.events).toHaveLength(3);
  });

  it("a stored log and a live stream give the same state", () => {
    seq = 0;
    const events = [ev("run", "run_queued"), ev("run", "run_started"), ev("intake", "stage_started"), ev("intake", "stage_completed", "completed"), ev("run", "run_completed", "completed", { decision: "BLOCK", risk: 0.9 })];
    const live = events.reduce((state, e) => applyEvent(state, e), initialRun());
    expect(replay(events)).toEqual(live);
    expect(live.runStatus).toBe("completed");
    expect(live.decision).toBe("BLOCK");
  });

  it("separates a stage that has not started from one whose telemetry is missing", () => {
    seq = 0;
    const queued = replay([ev("run", "run_queued")]);
    expect(stageStatus(queued, stage("intake"))).toBe("waiting");
    expect(stageStatus(queued, stage("verify"))).toBe("pending");
    const failed = replay([ev("run", "run_started"), ev("verify", "stage_started"), ev("verify", "stage_failed", "failed", { error: "boom" }, "boom"), ev("run", "run_failed", "failed", {}, "boom")]);
    expect(stageStatus(failed, stage("verify"))).toBe("failed");
    expect(failed.stages.verify.error).toBe("boom");
    expect(stageStatus(failed, stage("score"))).toBe("pending"); // finished run, never started: no telemetry
    expect(stageStatus(failed, stage("canary"))).toBe("skipped");
  });

  it("groups the verifier's sub-steps into tests, coverage and mutants", () => {
    seq = 0;
    const run = replay([
      ev("verify", "stage_started"),
      ev("verify", "substep", "running", { step: "tests", tests: 10 }),
      ev("verify", "substep", "completed", { step: "tests", passed: 10, failed: [] }),
      ev("verify", "substep", "info", { step: "mutant", status: "survived", file: "a.py", line: 3 }),
      ev("verify", "substep", "info", { step: "mutant", status: "killed", file: "a.py", line: 4 }),
      ev("verify", "substep", "completed", { step: "mutation", score: 0.5 }),
    ]);
    const steps = verifySteps(run.stages.verify);
    expect(steps.tests.passed).toBe(10);
    expect(steps.mutation.score).toBe(0.5);
    expect(steps.mutants.map((m) => m.status)).toEqual(["survived", "killed"]);
  });
});

describe("pipeline layout", () => {
  it("places the three agents in one row, between diff parsing and test selection", () => {
    const rows = layers(pipeline.stages, pipeline.edges).map((r) => r.map((s) => s.id));
    const agents = rows.find((r) => r.includes("impact"));
    expect(agents.sort()).toEqual(["historian", "impact", "signals"]);
    expect(rows.findIndex((r) => r.includes("parse_diff"))).toBeLessThan(rows.indexOf(agents));
    expect(rows.findIndex((r) => r.includes("select_tests"))).toBeGreaterThan(rows.indexOf(agents));
    expect(rows.flat()).toHaveLength(pipeline.stages.length);
  });
});
