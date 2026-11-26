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

export function decisionBase(decision) {
  if (!decision) return "BLOCK";
  return String(decision).split("_")[0];
}

export function decisionLevel(decision) {
  const parts = String(decision || "").split("_");
  return parts.length > 1 ? parts[1] : null;
}

export const DECISION_COLORS = {
  PASS: "#36a64f", PASS_LOW: "#36a64f", PASS_MEDIUM: "#2eb886", PASS_HIGH: "#daa520",
  CANARY: "#daa520", CANARY_LOW: "#7ecef4", CANARY_MEDIUM: "#daa520", CANARY_HIGH: "#e8812d",
  BLOCK: "#e01e5a", BLOCK_LOW: "#e8812d", BLOCK_MEDIUM: "#e01e5a", BLOCK_HIGH: "#c0392b",
};

export const DECISION_ICONS = {
  PASS: "✅", PASS_LOW: "✅", PASS_MEDIUM: "✅", PASS_HIGH: "⚠️",
  CANARY: "🟡", CANARY_LOW: "🟡", CANARY_MEDIUM: "🟡", CANARY_HIGH: "🟠",
  BLOCK: "⛔", BLOCK_LOW: "🔴", BLOCK_MEDIUM: "⛔", BLOCK_HIGH: "🚫",
};

export const DECISION_LABEL = {
  PASS_LOW: "Very Low Risk", PASS_MEDIUM: "Low-Medium Risk", PASS_HIGH: "Borderline Safe",
  CANARY_LOW: "Low Canary Risk", CANARY_MEDIUM: "Moderate Canary Risk", CANARY_HIGH: "High Canary Risk",
  BLOCK_LOW: "Borderline Block", BLOCK_MEDIUM: "Significant Risk", BLOCK_HIGH: "Critical Risk",
  PASS: "Safe", CANARY: "Caution", BLOCK: "High Risk",
};

export const decisionStyle = {
  PASS: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/40",
  PASS_LOW: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/40",
  PASS_MEDIUM: "bg-teal-500/15 text-teal-300 ring-1 ring-teal-500/40",
  PASS_HIGH: "bg-lime-500/15 text-lime-300 ring-1 ring-lime-500/40",
  CANARY: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/40",
  CANARY_LOW: "bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/40",
  CANARY_MEDIUM: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/40",
  CANARY_HIGH: "bg-orange-500/15 text-orange-300 ring-1 ring-orange-500/40",
  BLOCK: "bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/40",
  BLOCK_LOW: "bg-orange-500/15 text-orange-300 ring-1 ring-orange-500/40",
  BLOCK_MEDIUM: "bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/40",
  BLOCK_HIGH: "bg-red-600/20 text-red-300 ring-1 ring-red-500/50",
};

export const statusStyle = {
  VERIFIED: "bg-emerald-500/15 text-emerald-300",
  REFUTED: "bg-rose-500/15 text-rose-300",
  UNVERIFIABLE: "bg-slate-500/20 text-slate-300",
  UNCHECKED: "bg-slate-700/40 text-slate-400",
};

export const pct = (v, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const num = (v, digits = 2) => (v == null ? "—" : Number(v).toFixed(digits));
