import { AlertTriangle, Cpu } from "lucide-react";
import { duration } from "../../lib/format.js";
import { Banner, Card, cx } from "../../ui.jsx";

/** Which models a run actually used, what they cost in tokens, and every fallback or failure along the way. */
export default function LlmUsage({ report, meta }) {
  const llm = report?.llm;
  if (!llm) return null;
  const calls = llm.calls_detail || [];
  const used = calls.filter((c) => !c.error);
  const failed = calls.filter((c) => c.error);
  const asked = meta?.options?.llm !== false;
  const tokens = calls.filter((c) => !c.cache_hit && !c.error).reduce((n, c) => n + c.prompt_tokens + c.completion_tokens, 0);
  const providers = [...new Set(used.map((c) => `${c.provider}:${c.model}`))];

  let reason = null;
  if (!calls.length) {
    reason = !asked ? "LLM assistance was switched off for this run."
      : report.pr?.is_fork ? "This is a fork pull request: cloud models are not used on forks (no secrets are exposed to them)."
        : !llm.available ? "No model provider is configured on the server. See Integration Settings."
          : "The agents did not need to ask a model.";
  }
  return (
    <Card title="AI assistance" action={<span className={cx("pill", calls.length ? "bg-brand/15 text-brand" : "bg-s3 text-muted")}><Cpu size={12} aria-hidden />{calls.length ? "LLM-assisted" : "Evidence-only"}</span>}>
      {reason && <Banner title="Evidence-only mode">{reason} The verdict is unaffected: it never depends on model output.</Banner>}
      {calls.length > 0 && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div><div className="text-xs text-faint">Models used</div>{providers.length ? providers.map((p) => <div key={p} className="mono break-all">{p}</div>) : <span className="text-muted">none succeeded</span>}</div>
            <div><div className="text-xs text-faint">Tokens (billed)</div><div className="mono">{tokens}</div></div>
            <div><div className="text-xs text-faint">Calls</div><div className="mono">{calls.length} ({calls.filter((c) => c.cache_hit).length} cached, {failed.length} failed)</div></div>
            <div><div className="text-xs text-faint">Cost</div><div className="text-muted">Not reported by the providers (free tiers)</div></div>
          </div>
          {failed.length > 0 && <Banner tone="warn" title="Fallbacks and failures">{failed.map((c) => `${c.task}: ${c.provider} → ${c.error}`).join("; ")}. The router tried the next provider in the chain.</Banner>}
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>Task</th><th>Provider and model</th><th>Tier</th><th className="text-right">Tokens</th><th className="text-right">Latency</th><th>Result</th></tr></thead>
              <tbody>
                {calls.map((c, i) => (
                  <tr key={i}>
                    <td>{c.task}</td><td className="mono break-all">{c.provider}:{c.model}</td><td className="text-muted">{c.tier}</td>
                    <td className="mono text-right">{c.prompt_tokens + c.completion_tokens}</td><td className="mono text-right">{c.cache_hit ? "cached" : duration(c.latency_s)}</td>
                    <td>{c.error ? <span className="inline-flex items-center gap-1 text-warn"><AlertTriangle size={12} aria-hidden />{c.error}</span> : <span className="text-ok">ok</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-faint">Model output is untrusted input: claims must cite evidence the system gave it, are then checked by execution, and generated text cannot change the decision. Prompts: {Object.entries(report.prompts || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "n/a"}.</p>
        </div>
      )}
    </Card>
  );
}
