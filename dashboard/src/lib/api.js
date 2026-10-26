// Client for the SentinelPR service API (sentinel/service/api.py).
import { useCallback, useEffect, useRef, useState } from "react";

const BASE_KEY = "sentinelpr.api";
const TOKEN_KEY = "sentinelpr.token";

const read = (key) => {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
};

export const apiBase = () => read(BASE_KEY) || "http://127.0.0.1:8765";
export const apiToken = () => read(TOKEN_KEY);
export function saveConnection({ base, token }) {
  try {
    base ? localStorage.setItem(BASE_KEY, base) : localStorage.removeItem(BASE_KEY);
    token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the defaults still work */
  }
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error" } = {}) {
    super(message);
    this.status = status;
    this.code = code;
  }
  get unreachable() {
    return this.status === 0;
  }
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (apiToken()) headers.Authorization = `Bearer ${apiToken()}`;
  let response;
  try {
    response = await fetch(`${apiBase()}/api${path}`, { method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(`Cannot reach the SentinelPR server at ${apiBase()}. Start it with: python -m sentinel.api`, { status: 0, code: "unreachable" });
  }
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON error body */
  }
  if (!response.ok) {
    const err = data?.error || (typeof data?.detail === "string" ? { message: data.detail } : data?.detail) || {};
    throw new ApiError(err.message || `Request failed (${response.status})`, { status: response.status, code: err.code || "http_error" });
  }
  return data;
}

export const streamUrl = (runId, after = 0) => {
  const params = new URLSearchParams({ after: String(after) });
  if (apiToken()) params.set("token", apiToken()); // EventSource cannot set headers
  return `${apiBase()}/api/analyses/${encodeURIComponent(runId)}/stream?${params}`;
};

/** Fetch a path and keep it fresh. `interval` (ms) polls; `enabled=false` pauses. */
export function useApi(path, { interval = 0, enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: Boolean(path) && enabled });
  const alive = useRef(true);
  const load = useCallback(
    async (silent = false) => {
      if (!path || !enabled) return;
      if (!silent) setState((s) => ({ ...s, loading: true }));
      try {
        const data = await api(path);
        if (alive.current) setState({ data, error: null, loading: false });
      } catch (error) {
        if (alive.current && error.name !== "AbortError") setState((s) => ({ data: silent ? s.data : null, error, loading: false }));
      }
    },
    [path, enabled],
  );
  useEffect(() => {
    alive.current = true;
    setState({ data: null, error: null, loading: Boolean(path) && enabled });
    load();
    const id = interval && enabled ? setInterval(() => load(true), interval) : null;
    return () => {
      alive.current = false;
      if (id) clearInterval(id);
    };
  }, [path, interval, enabled, load]);
  return { ...state, reload: () => load(true), refetch: () => load(false) };
}
