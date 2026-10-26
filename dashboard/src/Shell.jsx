import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Link, useLocation, useNavigate } from "react-router-dom";
import {
  Activity, BellRing, BookOpenCheck, Boxes, ChevronRight, FlaskConical, FolderGit2, Gauge, GitPullRequest, History, LayoutDashboard, Menu, MessagesSquare, Network, Plug,
  Radio, Rocket, ScanSearch, ShieldCheck, Shield, Server, Terminal, X,
} from "lucide-react";
import { useApi } from "./lib/api.js";
import { ago } from "./lib/format.js";
import { Segmented, cx, useMode, RunStatus } from "./ui.jsx";

const NAV = [
  { title: "Workspace", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
    { to: "/repos", label: "Repositories", icon: FolderGit2 },
    { to: "/pulls", label: "Pull Requests", icon: GitPullRequest },
    { to: "/runs", label: "Analysis Runs", icon: Activity },
  ] },
  { title: "Insights", items: [
    { to: "/risk", label: "Risk Intelligence", icon: Gauge },
    { to: "/evidence", label: "Verification Evidence", icon: ScanSearch },
    { to: "/releases", label: "Release History", icon: History },
    { to: "/canary", label: "Canary Deployments", icon: Rocket },
  ] },
  { title: "Developer Tools", items: [
    { to: "/live", label: "Live Execution", icon: Radio },
    { to: "/graph", label: "Code Graph", icon: Network },
    { to: "/settings", label: "Integration Settings", icon: Plug },
    { to: "/health", label: "System Health", icon: Server },
  ] },
  { title: "Research", items: [
    { to: "/eval", label: "Evaluation Lab", icon: FlaskConical },
    { to: "/guardrails", label: "Guardrails", icon: Shield },
    { to: "/cost", label: "Cost & Latency", icon: Boxes },
    { to: "/ask", label: "Ask SentinelPR", icon: MessagesSquare },
  ] },
];

// Pages describe where they are (repository, PR, run) so the top bar can show it.
const TopbarContext = createContext({ crumbs: [], setCrumbs: () => {} });
export const useTopbar = () => useContext(TopbarContext);

export function useCrumbs(crumbs) {
  const { setCrumbs } = useTopbar();
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    setCrumbs(crumbs);
    return () => setCrumbs([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2.5 px-2" aria-label="SentinelPR home">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand/15 text-brand"><ShieldCheck size={17} aria-hidden /></span>
      <span className="text-[15px] font-semibold tracking-tight text-ink">SentinelPR</span>
    </Link>
  );
}

function Sidebar({ onNavigate }) {
  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-5 overflow-y-auto p-3">
      <div className="px-1 pb-1 pt-1"><Logo /></div>
      {NAV.map((group) => (
        <div key={group.title}>
          <div className="mb-1 px-3 text-[11px] font-medium uppercase tracking-wider text-faint">{group.title}</div>
          <ul className="space-y-0.5">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to} end={item.end} onClick={onNavigate}
                  className={({ isActive }) => cx("flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px] transition",
                    isActive ? "bg-s3 text-ink" : "text-muted hover:bg-s2 hover:text-ink")}
                >
                  <item.icon size={15} aria-hidden /> {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="mt-auto px-3 pb-2 text-[11px] leading-relaxed text-faint">
        LLMs propose. Execution verifies. A calibrated model decides.
      </p>
    </nav>
  );
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const runs = useApi("/analyses?limit=8", { interval: 4000 });
  const ref = useRef(null);
  useEffect(() => {
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const list = runs.data || [];
  const active = list.filter((r) => r.status === "running" || r.status === "queued");
  return (
    <div className="relative" ref={ref}>
      <button type="button" aria-label={`Notifications${active.length ? `, ${active.length} analyses running` : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-s2 hover:text-ink">
        <BellRing size={16} aria-hidden />
        {active.length > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-brand" />}
      </button>
      {open && (
        <div className="rise absolute right-0 top-10 z-40 w-80 rounded-lg border border-line2 bg-s1 p-2 shadow-2xl">
          <div className="px-2 py-1.5 text-xs font-medium text-faint">Recent analyses</div>
          {list.length === 0 && <div className="px-2 py-4 text-sm text-muted">Nothing has run yet.</div>}
          {list.map((r) => (
            <Link key={r.run_id} to={`/runs/${r.run_id}`} onClick={() => setOpen(false)} className="flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-s2">
              <span className="min-w-0">
                <span className="block truncate text-[13px] text-ink">{r.repo_name} · {r.pr.number != null ? `#${r.pr.number}` : r.pr.ref}</span>
                <span className="text-xs text-faint">{ago(r.created_at)}{r.decision ? ` · ${r.decision}` : ""}</span>
              </span>
              <RunStatus status={r.status} compact />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Topbar({ onMenu }) {
  const { crumbs } = useTopbar();
  const { mode, setMode } = useMode();
  const navigate = useNavigate();
  const location = useLocation();
  const onRunPage = /^\/(repos\/[^/]+\/pulls\/|runs\/)/.test(location.pathname);
  const change = (m) => {
    setMode(m);
    if (m === "live" && !onRunPage) navigate("/live");
    if (m === "product" && location.pathname === "/live") navigate("/");
  };
  return (
    <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-line bg-bg/90 px-4 backdrop-blur">
      <button type="button" aria-label="Open navigation" onClick={onMenu} className="rounded p-1.5 text-muted hover:bg-s2 lg:hidden"><Menu size={18} /></button>
      <div className="hidden items-center gap-2 text-[13px] sm:flex">
        <span className="rounded border border-line2 bg-s2 px-2 py-0.5 text-muted" title="No accounts or organisations: a single local workspace">Local workspace</span>
        {crumbs.map((c, i) => (
          <span key={i} className="flex min-w-0 items-center gap-2">
            <ChevronRight size={13} className="text-faint" aria-hidden />
            {c.to ? <Link to={c.to} className="max-w-48 truncate text-muted hover:text-ink">{c.label}</Link> : <span className="max-w-64 truncate text-ink">{c.label}</span>}
          </span>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-3">
        <Segmented label="View mode" value={mode} onChange={change} options={[
          { id: "product", label: "Product View", icon: BookOpenCheck },
          { id: "live", label: "Live Execution", icon: Terminal },
        ]} />
        <Notifications />
      </div>
    </header>
  );
}

export default function Shell({ children }) {
  const [drawer, setDrawer] = useState(false);
  const [crumbs, setCrumbs] = useState([]);
  const value = useMemo(() => ({ crumbs, setCrumbs }), [crumbs]);
  const location = useLocation();
  useEffect(() => setDrawer(false), [location.pathname]);
  return (
    <TopbarContext.Provider value={value}>
      <div className="flex min-h-screen">
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r border-line bg-s1 lg:block"><Sidebar /></aside>
        {drawer && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
            <aside className="rise relative h-full w-64 border-r border-line bg-s1">
              <button type="button" aria-label="Close navigation" onClick={() => setDrawer(false)} className="absolute right-2 top-2 rounded p-1.5 text-muted hover:bg-s2"><X size={16} /></button>
              <Sidebar onNavigate={() => setDrawer(false)} />
            </aside>
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar onMenu={() => setDrawer(true)} />
          <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
        </div>
      </div>
    </TopbarContext.Provider>
  );
}
