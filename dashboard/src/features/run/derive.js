// Plain-language findings and next actions, derived only from fields the report already contains.
import { pct } from "../../lib/format.js";

const tests = (r) => r.verification?.tests || {};
const mutation = (r) => r.verification?.mutation || {};
const lines = (r) => r.verification?.changed_lines || {};

export function findings(r) {
  const out = [];
  const failed = tests(r).failed || [];
  if (r.rejected) out.push({ tone: "bad", title: "Rejected by the input guard", detail: r.decision_reason });
  if (failed.length) out.push({ tone: "bad", title: `${failed.length} test${failed.length > 1 ? "s" : ""} fail on the head revision`, detail: failed.slice(0, 3).map((f) => f.nodeid.split("::").pop()).join(", ") });
  const uncovered = lines(r).uncovered || [];
  if (lines(r).total && uncovered.length) out.push({ tone: "warn", title: `${uncovered.length} of ${lines(r).total} changed lines are never executed by a test`, detail: uncovered.slice(0, 3).join(", ") });
  const survived = (mutation(r).mutants || []).filter((m) => m.status === "survived");
  if (survived.length) out.push({ tone: "warn", title: `${survived.length} of ${(mutation(r).mutants || []).length} mutants on the changed lines went unnoticed`, detail: `Mutation score ${pct(mutation(r).score)}: the tests would not catch these edits.` });
  const bugs = (r.claims || []).filter((c) => c.type === "history_link" && c.status === "VERIFIED");
  if (bugs.length) out.push({ tone: "warn", title: `Touches code with a verified bug history`, detail: bugs.slice(0, 2).map((c) => c.reason || c.target).join(" · ") });
  const apis = (r.claims || []).filter((c) => c.type === "api_break" && c.status === "VERIFIED");
  if (apis.length) out.push({ tone: "bad", title: `${apis.length} verified API break${apis.length > 1 ? "s" : ""}`, detail: apis.map((c) => c.target).join(", ") });
  const refuted = (r.claims || []).filter((c) => c.status === "REFUTED");
  if (refuted.length) out.push({ tone: "info", title: `${refuted.length} claim${refuted.length > 1 ? "s were" : " was"} refuted by execution`, detail: "The agents' predictions were checked and did not hold; they were not used as evidence." });
  const flagged = (r.guard_events || []).filter((e) => e.stage === "in" && (e.severity === "warn" || e.severity === "block") && e.guard !== "size");
  if (flagged.length) out.push({ tone: "warn", title: `Input guard flagged ${flagged.length} item${flagged.length > 1 ? "s" : ""}`, detail: [...new Set(flagged.map((e) => e.guard))].join(", ") });
  if (!out.length && r.verification) out.push({ tone: "ok", title: "No red flags in the verified evidence", detail: "Tests pass, changed lines are executed and mutants were caught." });
  return out;
}

export function nextActions(r) {
  const out = [];
  (tests(r).failed || []).slice(0, 5).forEach((f) => out.push(`Fix the failing test ${f.nodeid.split("::").pop()}.`));
  (lines(r).uncovered || []).slice(0, 4).forEach((ref) => out.push(`Add a test that executes ${ref}.`));
  (mutation(r).mutants || []).filter((m) => m.status === "survived").slice(0, 4).forEach((m) => out.push(`Strengthen assertions around ${m.file}#L${m.line}: changing "${m.original}" to "${m.mutated}" goes unnoticed.`));
  if (r.decision === "CANARY") out.push("Merge, then release through the canary rollout (10% → 25% → 50% → 100%) with automatic rollback.");
  if (r.decision === "PASS" && !out.length) out.push("Merge normally. Nothing in the verified evidence calls for extra caution.");
  if (r.decision === "BLOCK" && !out.length) out.push(r.decision_reason || "Address the blocking reason above, then re-run the analysis.");
  return out;
}

/** Impacted modules and tests that execution confirmed. */
export function verifiedImpact(r) {
  const claims = (r.claims || []).filter((c) => c.status === "VERIFIED");
  return { modules: claims.filter((c) => c.type === "module_impact"), tests: claims.filter((c) => c.type === "test_impact"), apis: claims.filter((c) => c.type === "api_break") };
}

export function claimContext(r, claim) {
  const failed = new Set((tests(r).failed || []).map((f) => f.nodeid));
  const selected = new Set(r.selection?.tests || []);
  const file = claim.target.includes("::") ? claim.target.split("::")[0] : claim.target;
  const mutants = (mutation(r).mutants || []).filter((m) => m.file === file || (m.killed_by || []).includes(claim.target));
  const uncovered = (lines(r).uncovered || []).filter((u) => u.startsWith(file));
  const test = claim.type === "test_impact" ? (failed.has(claim.target) ? "failed on the head revision" : selected.has(claim.target) ? "ran on the head revision and passed" : "was not run") : null;
  return { test, mutants, uncovered, verdict: (r.verdicts || []).find((v) => v.claim_id === claim.claim_id) };
}

/**
 * Contribution rows as the model computed them, with one honest relabel: when no LLM ran, the model still
 * applies its neutral default rating (0.5) to the LLM feature, which is not an opinion anyone gave.
 */
export function annotateContributions(contributions, report) {
  const noMutants = report?.verification && report.verification.mutation?.score == null;
  return (contributions || []).map((c) => {
    if (c.feature === "llm_risk" && report?.llm_risk == null) return { ...c, label: "LLM risk rating: no LLM ran, so the model's neutral default (0.5) is applied" };
    if (c.feature === "mutation_score" && noMutants) return { ...c, label: "Mutation score: no mutants were evaluated, but the model counts a missing score as 100% killed" };
    return c;
  });
}
