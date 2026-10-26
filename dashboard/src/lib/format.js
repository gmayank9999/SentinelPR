export const short = (sha) => (sha ? String(sha).slice(0, 7) : "—");

export function duration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "Not measured";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}

export function clock(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function stamp(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(ts) {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const pct = (v, digits = 0) => (v == null ? "Not measured" : `${(v * 100).toFixed(digits)}%`);
export const num = (v, digits = 2) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(digits));
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function prLabel(pr) {
  if (!pr) return "";
  return pr.number != null ? `#${pr.number}` : pr.ref || "";
}

export function repoPath(repoId, ref) {
  return `/repos/${encodeURIComponent(repoId)}/pulls/${encodeURIComponent(ref)}`;
}
