// Static data published next to the dashboard (see sentinel/publish/dashboard_data.py),
// plus the optional live API for Q&A and graph exploration.
import { useEffect, useState } from "react";

const cache = new Map();

export async function loadJSON(path) {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(`./data/${path}`).then((r) => {
        if (!r.ok) throw new Error(`${path}: ${r.status}`);
        return r.json();
      }),
    );
  }
  return cache.get(path);
}

export function useData(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    if (!path) return;
    let alive = true;
    loadJSON(path)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }));
    return () => {
      alive = false;
    };
  }, [path]);
  return state;
}

export const API_KEY = "sentinelpr.api";

export function apiBase() {
  return localStorage.getItem(API_KEY) || "http://127.0.0.1:8765";
}

export async function api(path, options = {}) {
  const response = await fetch(`${apiBase()}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

export const decisionStyle = {
  PASS: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/40",
  CANARY: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/40",
  BLOCK: "bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/40",
};

export const statusStyle = {
  VERIFIED: "bg-emerald-500/15 text-emerald-300",
  REFUTED: "bg-rose-500/15 text-rose-300",
  UNVERIFIABLE: "bg-slate-500/20 text-slate-300",
  UNCHECKED: "bg-slate-700/40 text-slate-400",
};

export const pct = (v, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const num = (v, digits = 2) => (v == null ? "—" : Number(v).toFixed(digits));
