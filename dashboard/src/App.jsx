import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import Shell from "./Shell.jsx";
import { ModeProvider, SkeletonRows, ToastProvider } from "./ui.jsx";

// Pages load on demand so the charting library is only fetched where it is used.
const Overview = lazy(() => import("./pages/product/Overview.jsx"));
const Repositories = lazy(() => import("./pages/product/Repositories.jsx"));
const RepoDetail = lazy(() => import("./pages/product/RepoDetail.jsx"));
const PullRequests = lazy(() => import("./pages/product/PullRequests.jsx"));
const PRWorkspace = lazy(() => import("./pages/product/PRWorkspace.jsx"));
const Runs = lazy(() => import("./pages/product/Runs.jsx"));
const Compare = lazy(() => import("./pages/product/Compare.jsx"));
const RiskIntelligence = lazy(() => import("./pages/product/RiskIntelligence.jsx"));
const EvidenceHub = lazy(() => import("./pages/product/EvidenceHub.jsx"));
const ReleaseHistory = lazy(() => import("./pages/product/ReleaseHistory.jsx"));
const Live = lazy(() => import("./pages/product/Live.jsx"));
const Settings = lazy(() => import("./pages/product/Settings.jsx"));
const Health = lazy(() => import("./pages/product/Health.jsx"));
const Canary = lazy(() => import("./pages/CanaryMonitor.jsx"));
const Deployments = lazy(() => import("./pages/product/Deployments.jsx"));
const CodeGraph = lazy(() => import("./pages/Evidence.jsx"));
const EvaluationLab = lazy(() => import("./pages/EvaluationLab.jsx"));
const Guardrails = lazy(() => import("./pages/Guardrails.jsx"));
const CostLatency = lazy(() => import("./pages/CostLatency.jsx"));
const Ask = lazy(() => import("./pages/Ask.jsx"));

export default function App() {
  return (
    <ModeProvider>
      <ToastProvider>
        <Shell>
          <Suspense fallback={<SkeletonRows rows={5} />}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/repos" element={<Repositories />} />
              <Route path="/repos/:repoId" element={<RepoDetail />} />
              <Route path="/repos/:repoId/pulls/:ref" element={<PRWorkspace />} />
              <Route path="/pulls" element={<PullRequests />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/:runId" element={<PRWorkspace />} />
              <Route path="/compare" element={<Compare />} />
              <Route path="/risk" element={<RiskIntelligence />} />
              <Route path="/evidence" element={<EvidenceHub />} />
              <Route path="/releases" element={<ReleaseHistory />} />
              <Route path="/canary" element={<Deployments />} />
              <Route path="/canary/recorded" element={<Canary />} />
              <Route path="/live" element={<Live />} />
              <Route path="/graph" element={<CodeGraph />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/health" element={<Health />} />
              <Route path="/eval" element={<EvaluationLab />} />
              <Route path="/guardrails" element={<Guardrails />} />
              <Route path="/cost" element={<CostLatency />} />
              <Route path="/ask" element={<Ask />} />
              <Route path="*" element={<div className="text-muted">Page not found.</div>} />
            </Routes>
          </Suspense>
        </Shell>
      </ToastProvider>
    </ModeProvider>
  );
}
