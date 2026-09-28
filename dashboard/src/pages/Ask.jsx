import { useState } from "react";
import { PageHeader } from "../components.jsx";
import { API_KEY, api, apiBase } from "../data.js";

const EXAMPLES = [
  "Why does fee calculation round down?",
  "Has calculateStudentCredits had bugs before?",
  "Why is the attendance threshold 75%?",
  "Where is the late fee computed?",
];

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState("code_history");
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [base, setBase] = useState(apiBase());

  const ask = async (q) => {
    const text = (q ?? question).trim();
    if (!text) return;
    setBusy(true);
    setQuestion("");
    try {
      const answer = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: text, mode }) });
      setHistory((h) => [{ q: text, a: answer }, ...h]);
    } catch (e) {
      setHistory((h) => [{ q: text, error: `Could not reach the SentinelPR API at ${apiBase()} (${e.message}). Start it with: python -m sentinel.api` }, ...h]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-4xl">
      <PageHeader title="Ask SentinelPR" subtitle="Questions about the code and its history, answered only from the repository with a citation for every sentence — or an honest refusal." />
      <div className="card">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder="Why is the code like this?" className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <select value={mode} onChange={(e) => setMode(e.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-2 text-sm">
            <option value="code_history">code + history</option><option value="code">code only</option><option value="no_rag">no retrieval</option>
          </select>
          <button disabled={busy} onClick={() => ask()} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500 disabled:opacity-50">{busy ? "…" : "Ask"}</button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => <button key={e} onClick={() => ask(e)} className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-400 hover:text-slate-200">{e}</button>)}
        </div>
        <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
          API
          <input value={base} onChange={(e) => setBase(e.target.value)} onBlur={() => localStorage.setItem(API_KEY, base)} className="w-64 rounded border border-slate-800 bg-slate-950 px-2 py-1" />
        </div>
      </div>
      <div className="mt-6 space-y-4">
        {history.map((item, i) => (
          <div key={i} className="card">
            <div className="text-sm font-medium text-slate-200">{item.q}</div>
            {item.error ? <p className="mt-2 text-sm text-rose-300">{item.error}</p> : (
              <>
                <p className={`mt-2 text-sm leading-relaxed ${item.a.refused ? "text-amber-200" : "text-slate-300"}`}>{item.a.answer}</p>
                {item.a.citations?.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">{item.a.citations.map((c) => <code key={c} className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-emerald-300">{c}</code>)}</div>
                )}
                <div className="mt-2 text-xs text-slate-600">{item.a.source} · {item.a.mode} · {item.a.latency_s}s</div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
