import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Banner, ClaimStatus, EmptyState, KV } from "../../ui.jsx";
import { claimContext } from "./derive.js";
import { ClaimsTable, EvidenceChip, MutantsTable, evidenceKind } from "./parts.jsx";

const STATUSES = ["VERIFIED", "REFUTED", "UNVERIFIABLE"];

function Detail({ report, claim }) {
  const ctx = claimContext(report, claim);
  const v = ctx.verdict;
  const status = v?.status || claim.status;
  return (
    <div className="rise space-y-4 rounded-lg border border-line bg-s2 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{claim.type.replace("_", " ")}</h3><ClaimStatus status={status} /></div>
      <p className="mono break-all">{claim.target}</p>
      <KV rows={[["Asserted by", `${claim.agent} agent${report.impact_mode === "llm" && claim.agent === "impact" ? " (LLM, restricted to the evidence it was given)" : " (deterministic rules)"}`],
        ["Original reasoning", claim.reason || "—"], ["Verification method", v?.method || "—"], ctx.test && ["Test outcome", ctx.test]]} />
      <div>
        <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">{status === "REFUTED" ? "Contradicting evidence" : status === "UNVERIFIABLE" ? "Why it could not be established" : "Supporting evidence"}</h4>
        {v?.detail && <p className="mb-2 text-sm text-muted">{v.detail}</p>}
        <div className="flex flex-wrap gap-1.5">{(v?.evidence || []).map((e) => <EvidenceChip key={e} id={e} />)}</div>
        {!v && <p className="text-sm text-muted">This claim was not checked (verification was disabled or the run was rejected before it).</p>}
      </div>
      <div>
        <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">Evidence the agent cited</h4>
        <ul className="space-y-1">{claim.evidence_ids.map((e) => <li key={e} className="flex items-center gap-2 text-sm"><EvidenceChip id={e} /><span className="text-faint">{evidenceKind(e).direct ? "observed" : "inferred"}</span></li>)}</ul>
      </div>
      {ctx.mutants.length > 0 && <div><h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-faint">Related mutation results</h4><MutantsTable mutants={ctx.mutants} /></div>}
      {ctx.uncovered.length > 0 && <Banner tone="warn" title="Changed lines no test executes">{ctx.uncovered.join(", ")}</Banner>}
    </div>
  );
}

/** Every claim with its verdict, filters, and the execution evidence behind the selected one. */
export default function EvidenceExplorer({ report }) {
  const [status, setStatus] = useState("all");
  const [agent, setAgent] = useState("all");
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(null);
  const claims = report.claims || [];
  const agents = [...new Set(claims.map((c) => c.agent))];
  const types = [...new Set(claims.map((c) => c.type))];
  const rows = useMemo(() => claims.filter((c) => (status === "all" || c.status === status) && (agent === "all" || c.agent === agent) && (type === "all" || c.type === type)
    && (!query || c.target.toLowerCase().includes(query.toLowerCase()))), [claims, status, agent, type, query]);
  const counts = Object.fromEntries(STATUSES.map((s) => [s, claims.filter((c) => c.status === s).length]));
  const current = claims.find((c) => c.claim_id === picked);

  if (!claims.length) return <EmptyState title="This run made no claims">The input guard may have rejected it before the agents ran.</EmptyState>;
  return (
    <div className="space-y-4">
      <Banner title="How claims get their status">A claim is VERIFIED only when execution backs it (a test that runs the changed lines, a mutant a test kills, a cited commit that exists). Model confidence never counts. REFUTED means execution contradicted it; UNVERIFIABLE means the evidence needed to decide was not available.</Banner>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" aria-hidden />
          <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter claims by file or test" placeholder="Filter by file or test…" className="h-8 w-full rounded-md border border-line2 bg-bg pl-8 pr-3 text-sm outline-none placeholder:text-faint focus:border-brand" /></div>
        {[["status", status, setStatus, ["all", ...STATUSES], (s) => (s === "all" ? "All statuses" : `${s.toLowerCase()} (${counts[s]})`)],
          ["agent", agent, setAgent, ["all", ...agents], (s) => (s === "all" ? "All agents" : s)],
          ["type", type, setType, ["all", ...types], (s) => (s === "all" ? "All types" : s.replace("_", " "))]].map(([label, value, set, options, show]) => (
          <select key={label} aria-label={`Filter by ${label}`} value={value} onChange={(e) => set(e.target.value)} className="h-8 rounded-md border border-line2 bg-bg px-2 text-sm">{options.map((o) => <option key={o} value={o}>{show(o)}</option>)}</select>))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-line bg-s1">
          {rows.length ? <ClaimsTable claims={rows} onPick={(c) => setPicked(c.claim_id)} picked={picked} /> : <div className="p-5 text-sm text-muted">No claims match these filters.</div>}
        </div>
        <div>{current ? <Detail report={report} claim={current} /> : <div className="rounded-lg border border-dashed border-line2 p-8 text-center text-sm text-muted">Select a claim to see how execution checked it.</div>}</div>
      </div>
    </div>
  );
}
