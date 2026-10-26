import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Ban, Check, CheckCircle2, Circle, Clock, Copy, HelpCircle, Info, Loader2, MinusCircle, ServerCrash, ShieldAlert, ShieldCheck, ShieldX, X, XCircle,
} from "lucide-react";

export const cx = (...parts) => parts.filter(Boolean).join(" ");

// ---------------------------------------------------------------------------------------------
// status vocabulary: every state has an icon AND a word, never colour alone

const DECISIONS = {
  PASS: { icon: ShieldCheck, tone: "text-ok bg-ok/10 ring-ok/30", word: "PASS" },
  CANARY: { icon: ShieldAlert, tone: "text-warn bg-warn/10 ring-warn/30", word: "CANARY" },
  BLOCK: { icon: ShieldX, tone: "text-bad bg-bad/10 ring-bad/30", word: "BLOCK" },
};

export function DecisionBadge({ decision, size = "sm" }) {
  const d = DECISIONS[decision];
  if (!d) return <span className="pill bg-s3 text-faint ring-1 ring-line2">No decision</span>;
  const Icon = d.icon;
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-md font-semibold ring-1", d.tone, size === "lg" ? "px-3 py-1.5 text-base" : "px-2 py-0.5 text-xs")}>
      <Icon size={size === "lg" ? 18 : 13} aria-hidden /> {d.word}
    </span>
  );
}

const RUN_STATUS = {
  queued: { icon: Clock, tone: "text-muted", label: "Queued" },
  running: { icon: Loader2, tone: "text-brand", label: "Running", spin: true },
  completed: { icon: CheckCircle2, tone: "text-ok", label: "Completed" },
  failed: { icon: XCircle, tone: "text-bad", label: "Failed" },
  cancelled: { icon: Ban, tone: "text-faint", label: "Cancelled" },
  unknown: { icon: Circle, tone: "text-faint", label: "Unknown" },
};

export function RunStatus({ status, compact }) {
  const s = RUN_STATUS[status] || RUN_STATUS.unknown;
  const Icon = s.icon;
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-xs font-medium", s.tone)}>
      <Icon size={13} className={s.spin ? "spin" : ""} aria-hidden /> {!compact && s.label}
    </span>
  );
}

export const STAGE = {
  pending: { icon: Circle, tone: "text-faint", label: "Pending" },
  waiting: { icon: Clock, tone: "text-muted", label: "Waiting" },
  running: { icon: Loader2, tone: "text-brand", label: "Running", spin: true },
  completed: { icon: CheckCircle2, tone: "text-ok", label: "Completed" },
  failed: { icon: XCircle, tone: "text-bad", label: "Failed" },
  skipped: { icon: MinusCircle, tone: "text-faint", label: "Skipped" },
};

export function StageIcon({ status, size = 16 }) {
  const s = STAGE[status] || STAGE.pending;
  const Icon = s.icon;
  return <Icon size={size} className={cx(s.tone, s.spin && "spin")} aria-label={s.label} />;
}

const CLAIM = {
  VERIFIED: { icon: CheckCircle2, tone: "text-ok bg-ok/10", label: "Verified" },
  REFUTED: { icon: XCircle, tone: "text-bad bg-bad/10", label: "Refuted" },
  UNVERIFIABLE: { icon: HelpCircle, tone: "text-warn bg-warn/10", label: "Unverifiable" },
  UNCHECKED: { icon: Circle, tone: "text-faint bg-s3", label: "Unchecked" },
};

export function ClaimStatus({ status }) {
  const c = CLAIM[status] || CLAIM.UNCHECKED;
  const Icon = c.icon;
  return (
    <span className={cx("pill", c.tone)}>
      <Icon size={12} aria-hidden /> {c.label}
    </span>
  );
}

// ---------------------------------------------------------------------------------------------
// controls

const BUTTON = {
  primary: "bg-brand text-bg hover:brightness-110 font-medium",
  secondary: "bg-s3 text-ink border border-line2 hover:bg-line hover:border-faint",
  ghost: "text-muted hover:text-ink hover:bg-s2",
  danger: "bg-bad/15 text-bad border border-bad/30 hover:bg-bad/25",
};

export function Button({ variant = "secondary", size = "md", icon: Icon, loading, children, className, ...props }) {
  return (
    <button
      type="button"
      {...props}
      disabled={props.disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm", BUTTON[variant], className,
      )}
    >
      {loading ? <Loader2 size={14} className="spin" aria-hidden /> : Icon ? <Icon size={size === "sm" ? 13 : 15} aria-hidden /> : null}
      {children}
    </button>
  );
}

export function Tabs({ tabs, value, onChange, label = "Sections" }) {
  const ref = useRef(null);
  const onKey = (e) => {
    const i = tabs.findIndex((t) => t.id === value);
    const move = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (move) {
      e.preventDefault();
      const next = tabs[(i + move + tabs.length) % tabs.length];
      onChange(next.id);
      ref.current?.querySelector(`[data-tab="${next.id}"]`)?.focus();
    }
  };
  return (
    <div ref={ref} role="tablist" aria-label={label} onKeyDown={onKey} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id} data-tab={t.id} role="tab" type="button" aria-selected={value === t.id} tabIndex={value === t.id ? 0 : -1}
          onClick={() => onChange(t.id)}
          className={cx("-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition",
            value === t.id ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink")}
        >
          {t.icon && <t.icon size={14} aria-hidden />}
          {t.label}
          {t.count != null && <span className="rounded bg-s3 px-1.5 text-[11px] text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Segmented({ options, value, onChange, label }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line2 bg-s2 p-0.5">
      {options.map((o) => (
        <button
          key={o.id} type="button" role="radio" aria-checked={value === o.id} onClick={() => onChange(o.id)}
          className={cx("inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition", value === o.id ? "bg-brand text-bg" : "text-muted hover:text-ink")}
        >
          {o.icon && <o.icon size={13} aria-hidden />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button" title={label} aria-label={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        } catch {
          /* clipboard unavailable */
        }
      }}
      className="inline-flex h-6 w-6 items-center justify-center rounded text-faint hover:bg-s3 hover:text-ink"
    >
      {done ? <Check size={13} className="text-ok" /> : <Copy size={13} />}
    </button>
  );
}

export function Id({ value, className }) {
  return (
    <span className={cx("inline-flex items-center gap-1", className)}>
      <span className="mono text-muted">{value}</span>
      <CopyButton text={value} />
    </span>
  );
}

// ---------------------------------------------------------------------------------------------
// states

export const Skeleton = ({ className = "h-4 w-full" }) => <div className={cx("skeleton", className)} aria-hidden />;

export function SkeletonRows({ rows = 4 }) {
  return (
    <div className="space-y-3 p-1" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  );
}

export function EmptyState({ icon: Icon = Info, title, children, action }) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-line2 px-6 py-12 text-center">
      <div className="mb-3 rounded-full bg-s3 p-3 text-muted"><Icon size={22} aria-hidden /></div>
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      {children && <p className="mt-1.5 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Something went wrong" }) {
  const unreachable = error?.unreachable;
  return (
    <div role="alert" className="flex flex-col items-center rounded-lg border border-bad/30 bg-bad/5 px-6 py-10 text-center">
      <div className="mb-3 rounded-full bg-bad/10 p-3 text-bad">{unreachable ? <ServerCrash size={22} aria-hidden /> : <AlertTriangle size={22} aria-hidden />}</div>
      <h3 className="text-sm font-medium text-ink">{unreachable ? "SentinelPR server is not reachable" : title}</h3>
      <p className="mt-1.5 max-w-lg text-sm text-muted">{error?.message || "Unknown error"}</p>
      {unreachable && <code className="mono mt-3 rounded bg-bg px-3 py-1.5 text-brand">python -m sentinel.api</code>}
      {onRetry && <Button className="mt-4" icon={null} onClick={onRetry}>Try again</Button>}
    </div>
  );
}

/** Renders loading / error / empty / content for a `useApi` result. */
export function Async({ state, skeleton, children, empty }) {
  if (state.loading) return skeleton || <SkeletonRows />;
  if (state.error) return <ErrorState error={state.error} onRetry={state.refetch} />;
  if (empty && (state.data == null || (Array.isArray(state.data) && state.data.length === 0))) return empty;
  return children(state.data);
}

export function Banner({ tone = "info", title, children }) {
  const tones = { info: "border-brand/30 bg-brand/5 text-brand", warn: "border-warn/30 bg-warn/5 text-warn", bad: "border-bad/30 bg-bad/5 text-bad" };
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div role="note" className={cx("flex gap-3 rounded-lg border p-3 text-sm", tones[tone])}>
      <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />
      <div className="text-muted">{title && <div className="mb-0.5 font-medium text-ink">{title}</div>}{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// layout helpers

export function Card({ title, action, children, className, pad = true }) {
  return (
    <section className={cx("rounded-lg border border-line bg-s1", pad && "p-5", className)}>
      {(title || action) && (
        <header className={cx("flex items-center justify-between gap-3", pad ? "mb-3" : "border-b border-line px-5 py-3")}>
          {title && <h2 className="text-xs font-medium uppercase tracking-wider text-faint">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions, back }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        {back}
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "text-ink", icon: Icon, to }) {
  const body = (
    <div className={cx("h-full rounded-lg border border-line bg-s1 p-4", to && "transition hover:border-line2 hover:bg-s2")}>
      <div className="flex items-center justify-between text-xs text-faint">{label}{Icon && <Icon size={14} aria-hidden />}</div>
      <div className={cx("mt-1.5 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </div>
  );
  return to ? <a href={`#${to}`} className="block">{body}</a> : body;
}

export function KV({ rows, className }) {
  return (
    <dl className={cx("grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm", className)}>
      {rows.filter(Boolean).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-faint">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Json({ value, label = "Raw data" }) {
  return (
    <details className="group">
      <summary className="cursor-pointer text-xs text-muted hover:text-ink">{label}</summary>
      <pre className="codeblock mt-2">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

/** Risk against the policy thresholds: where the score sits relative to CANARY and BLOCK. */
export function RiskMeter({ risk, thresholds }) {
  if (risk == null) return <span className="text-sm text-faint">Not measured</span>;
  const { canary = 0.35, block = 0.65 } = thresholds || {};
  const at = (v) => `${Math.min(Math.max(v, 0), 1) * 100}%`;
  return (
    <div role="img" aria-label={`Risk ${risk.toFixed(2)}; canary threshold ${canary}; block threshold ${block}`}>
      <div className="relative h-2.5 rounded-full bg-s3">
        <div className="absolute inset-y-0 left-0 rounded-l-full bg-ok/40" style={{ width: at(canary) }} />
        <div className="absolute inset-y-0 bg-warn/40" style={{ left: at(canary), width: `calc(${at(block)} - ${at(canary)})` }} />
        <div className="absolute inset-y-0 rounded-r-full bg-bad/40" style={{ left: at(block), right: 0 }} />
        <div className="absolute -top-1 h-4.5 w-1 rounded bg-ink shadow" style={{ left: `calc(${at(risk)} - 2px)`, height: 18 }} />
      </div>
      <div className="relative mt-1.5 h-4 text-[11px] text-faint">
        <span className="absolute -translate-x-1/2" style={{ left: at(canary) }}>canary {canary}</span>
        <span className="absolute -translate-x-1/2" style={{ left: at(block) }}>block {block}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// modal & toasts

export function Modal({ open, title, onClose, children, footer }) {
  const id = useId();
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.activeElement;
    box.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={id} className="rise w-full max-w-lg rounded-xl border border-line2 bg-s1 shadow-2xl outline-none">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 id={id} className="text-sm font-semibold">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-muted hover:bg-s3 hover:text-ink"><X size={16} /></button>
        </div>
        <div className="space-y-3 px-5 py-4 text-sm">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

const ToastContext = createContext({ push: () => {} });
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((message, tone = "info") => {
    const id = Math.random().toString(36).slice(2);
    setItems((list) => [...list, { id, message, tone }]);
    setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  const tones = { info: "text-brand", success: "text-ok", error: "text-bad" };
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="rise pointer-events-auto flex items-start gap-2 rounded-lg border border-line2 bg-s2 p-3 text-sm shadow-xl">
            {t.tone === "error" ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-bad" /> : <CheckCircle2 size={16} className={cx("mt-0.5 shrink-0", tones[t.tone])} />}
            <span className="text-ink">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ---------------------------------------------------------------------------------------------
// view mode: Product View | Live Execution (two presentations of the same run)

const ModeContext = createContext({ mode: "product", setMode: () => {} });
export const useMode = () => useContext(ModeContext);

export function ModeProvider({ children }) {
  const [mode, setModeState] = useState(() => {
    try {
      return localStorage.getItem("sentinelpr.mode") === "live" ? "live" : "product";
    } catch {
      return "product";
    }
  });
  const setMode = useCallback((m) => {
    setModeState(m);
    try {
      localStorage.setItem("sentinelpr.mode", m);
    } catch {
      /* ignore */
    }
  }, []);
  const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);
  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}
