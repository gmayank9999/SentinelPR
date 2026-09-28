import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

// Pages are loaded on demand so the charting library is only fetched where it is used.
const Overview = lazy(() => import("./pages/Overview.jsx"));
const Pipeline = lazy(() => import("./pages/Pipeline.jsx"));
const RunReport = lazy(() => import("./pages/RunReport.jsx"));
const Evidence = lazy(() => import("./pages/Evidence.jsx"));
const Guardrails = lazy(() => import("./pages/Guardrails.jsx"));
const EvaluationLab = lazy(() => import("./pages/EvaluationLab.jsx"));
const CostLatency = lazy(() => import("./pages/CostLatency.jsx"));
const CanaryMonitor = lazy(() => import("./pages/CanaryMonitor.jsx"));
const Ask = lazy(() => import("./pages/Ask.jsx"));

const NAV = [
  ["/", "Overview"],
  ["/pipeline", "Live Pipeline"],
  ["/runs", "PR Report"],
  ["/evidence", "Evidence Explorer"],
  ["/guardrails", "Guardrails"],
  ["/eval", "Evaluation Lab"],
  ["/cost", "Cost & Latency"],
  ["/canary", "Canary Monitor"],
  ["/ask", "Ask SentinelPR"],
];

export default function App() {
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-950 p-4 md:flex">
        <div className="mb-8 flex items-center gap-2">
          <svg viewBox="0 0 32 32" className="h-7 w-7 fill-emerald-500"><path d="M16 2 4 7v8c0 7.5 5.1 13.4 12 15 6.9-1.6 12-7.5 12-15V7z" /></svg>
          <div>
            <div className="font-semibold text-slate-100">SentinelPR</div>
            <div className="text-xs text-slate-500">evidence-gated releases</div>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm ${isActive ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"}`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <p className="mt-auto text-xs leading-relaxed text-slate-600">
          LLMs propose. Execution verifies. A calibrated model decides. The pipeline acts. A human approves.
        </p>
      </aside>
      <main className="min-w-0 flex-1 p-6 lg:p-8">
        <Suspense fallback={<div className="text-sm text-slate-500">Loading…</div>}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/runs" element={<RunReport />} />
            <Route path="/runs/:id" element={<RunReport />} />
            <Route path="/evidence" element={<Evidence />} />
            <Route path="/guardrails" element={<Guardrails />} />
            <Route path="/eval" element={<EvaluationLab />} />
            <Route path="/cost" element={<CostLatency />} />
            <Route path="/canary" element={<CanaryMonitor />} />
            <Route path="/ask" element={<Ask />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
