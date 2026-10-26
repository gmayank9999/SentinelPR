import { useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";
import { ClaimStatus, cx } from "../../ui.jsx";
import { num, pct } from "../../lib/format.js";

/** How a claim's evidence was obtained: direct observation versus inference. */
export function evidenceKind(id = "") {
  if (id.startsWith("cov:")) return { label: "recorded coverage", direct: true };
  if (id.startsWith("covsym:")) return { label: "coverage of the function", direct: true };
  if (id.startsWith("graph:")) return { label: "call graph (inferred)", direct: false };
  if (id.startsWith("sig:")) return { label: "signature change", direct: true };
  if (id.startsWith("chunk:")) return { label: "retrieved code", direct: false };
  if (id.startsWith("commit:") || id.startsWith("issue:") || id.startsWith("pr:")) return { label: "repository history", direct: true };
  return { label: "other", direct: false };
}

export function EvidenceChip({ id }) {
  const k = evidenceKind(id);
  return <span title={k.label} className={cx("mono inline-block max-w-full truncate rounded border px-1.5 py-0.5 align-middle text-[11px]", k.direct ? "border-ok/30 text-ok" : "border-line2 text-muted")}>{id}</span>;
}

export function ClaimsTable({ claims, statuses, onPick, picked }) {
  if (!claims?.length) return <p className="text-sm text-muted">No claims.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>Claim</th><th>Target</th><th>Status</th></tr></thead>
        <tbody>
          {claims.map((c) => (
            <tr key={c.claim_id} onClick={onPick ? () => onPick(c) : undefined}
              className={cx(onPick && "cursor-pointer hover:bg-s2/60", picked === c.claim_id && "bg-s2")}>
              <td className="whitespace-nowrap text-muted">{c.type.replace("_", " ")}<div className="mono text-faint">{c.agent}</div></td>
              <td className="mono break-all">{c.target}</td>
              <td><ClaimStatus status={statuses?.[c.claim_id] || c.status || "UNCHECKED"} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MutantsTable({ mutants }) {
  if (!mutants?.length) return <p className="text-sm text-muted">No mutants were generated for the changed lines.</p>;
  const tone = { killed: "text-ok", survived: "text-bad", timeout: "text-warn", error: "text-faint", pending: "text-faint" };
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>Location</th><th>Change</th><th>Outcome</th></tr></thead>
        <tbody>
          {mutants.map((m, i) => (
            <tr key={m.id || i}>
              <td className="mono whitespace-nowrap">{m.file}#L{m.line}<div className="text-faint">{m.operator}</div></td>
              <td className="mono"><div className="text-bad/90">− {m.original}</div><div className="text-ok/90">+ {m.mutated}</div></td>
              <td className={cx("whitespace-nowrap font-medium", tone[m.status])}>{m.status === "killed" ? "Killed" : m.status === "survived" ? "Survived" : m.status}
                {m.killed_by?.length ? <div className="mono text-[11px] font-normal text-faint">by {m.killed_by[0].split("::").pop()}</div> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ContributionBars({ contributions, max }) {
  if (!contributions?.length) return <p className="text-sm text-muted">No contributions recorded.</p>;
  const top = max || Math.max(...contributions.map((c) => Math.abs(c.contribution)), 0.01);
  return (
    <ul className="space-y-2.5">
      {contributions.map((c) => {
        const up = c.contribution > 0;
        return (
          <li key={c.feature}>
            <div className="mb-1 flex items-center justify-between gap-3 text-sm">
              <span className="flex items-center gap-1.5">{up ? <ArrowUp size={13} className="text-bad" aria-label="raises risk" /> : <ArrowDown size={13} className="text-ok" aria-label="lowers risk" />}{c.label || c.feature}</span>
              <span className="mono text-muted">value {num(c.value)} · {up ? "+" : ""}{num(c.contribution)}</span>
            </div>
            <div className="h-1.5 rounded bg-s3"><div className={cx("h-1.5 rounded", up ? "bg-bad/70" : "bg-ok/70")} style={{ width: `${Math.min(100, (Math.abs(c.contribution) / top) * 100)}%` }} /></div>
          </li>
        );
      })}
    </ul>
  );
}

export function Tiles({ items }) {
  return (
    <div className="grid grid-cols-2 gap-2 xl:grid-cols-[repeat(auto-fit,minmax(120px,1fr))]">
      {items.filter(Boolean).map(([label, value, hint, tone]) => (
        <div key={label} className="rounded-md border border-line bg-s2 p-3">
          <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
          <div className={cx("mt-1 text-lg font-semibold tabular-nums", tone || "text-ink")}>{value}</div>
          {hint && <div className="text-[11px] text-faint">{hint}</div>}
        </div>
      ))}
    </div>
  );
}

export function TestsList({ tests, reasons, limit = 12 }) {
  const [all, setAll] = useState(false);
  const shown = all ? tests : tests.slice(0, limit);
  return (
    <div>
      <ul className="space-y-1.5">
        {shown.map((t) => (
          <li key={t} className="text-sm"><span className="mono break-all">{t}</span>{reasons?.[t] && <div className="text-xs text-faint">{reasons[t]}</div>}</li>
        ))}
      </ul>
      {tests.length > limit && <button type="button" className="mt-2 text-xs text-brand hover:underline" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${tests.length}`}</button>}
    </div>
  );
}

export function Disclosure({ title, children, defaultOpen = false, meta }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-line">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-s2">
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <span className="font-medium">{title}</span><span className="ml-auto text-xs text-faint">{meta}</span>
      </button>
      {open && <div className="border-t border-line p-3">{children}</div>}
    </div>
  );
}

export function FeatureTable({ features, contributions }) {
  const byName = Object.fromEntries((contributions || []).map((c) => [c.feature, c]));
  const rows = Object.entries(features || {}).sort((a, b) => Math.abs(byName[b[0]]?.contribution || 0) - Math.abs(byName[a[0]]?.contribution || 0));
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>Feature</th><th className="text-right">Value</th><th className="text-right">Contribution</th></tr></thead>
        <tbody>
          {rows.map(([name, value]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td className="mono text-right">{num(value, 3)}</td>
              <td className="mono text-right text-muted">{byName[name] ? `${byName[name].contribution > 0 ? "+" : ""}${num(byName[name].contribution)}` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const coverageText = (cl) => (cl ? `${cl.covered} of ${cl.total} (${pct(cl.total ? cl.covered / cl.total : 1)})` : "Not measured");
