// Thin client for the CoreData FastAPI backend.
import { useEffect, useRef, useState, useCallback } from "react";

/** Provider API keys typed in the UI. Memory only (never localStorage/sessionStorage); sent with
 *  create / run / confirm requests. Blank keys fall back to the server's .env on the backend. */
export const keyStore = {
  anthropic_api_key: "",
  crustdata_api_key: "",
  set(k) {
    Object.assign(this, k);
  },
  body() {
    const b = {};
    if (this.anthropic_api_key.trim()) b.anthropic_api_key = this.anthropic_api_key.trim();
    if (this.crustdata_api_key.trim()) b.crustdata_api_key = this.crustdata_api_key.trim();
    return b;
  },
};

async function json(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const text = await r.text();
    let msg = text || r.statusText;
    try {
      const d = JSON.parse(text).detail;
      msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : msg;
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  health: () => json("/health"),
  list: () => json("/api/investigations"),
  create: (body) =>
    json("/api/investigations", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, ...keyStore.body() }),
    }),
  get: (id) => json(`/api/investigations/${id}`),
  insights: (id) => json(`/api/investigations/${id}/insights`),
  signals: (id) => json(`/api/investigations/${id}/signals`),
  report: (id) => json(`/api/investigations/${id}/report`),
  run: (id) =>
    json(`/api/investigations/${id}/run`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(keyStore.body()),
    }),
  confirm: (id, body) =>
    json(`/api/investigations/${id}/confirm_company`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, ...keyStore.body() }),
    }),
  watch: (id, item) =>
    json(`/api/investigations/${id}/watchlist`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(item) }),
};

export const DONE = ["completed", "completed_with_warnings"];
export const FINAL = [...DONE, "failed", "needs_confirmation"];

/** Live investigation state: SSE events + refresh of status/insights/signals/report. */
export function useInvestigation(id) {
  const [inv, setInv] = useState(null);
  const [insights, setInsights] = useState(null);
  const [signals, setSignals] = useState([]);
  const [report, setReport] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const i = await api.get(id);
      setInv(i);
      const [ins, sig] = await Promise.all([api.insights(id), api.signals(id)]);
      setInsights(ins);
      setSignals(sig);
      if (DONE.includes(i.status)) setReport(await api.report(id).catch(() => null));
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [id]);

  useEffect(() => {
    setEvents([]);
    setReport(null);
    refresh();
    const es = new EventSource(`/api/investigations/${id}/events`);
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e]));
      // debounce refreshes while the run streams events
      clearTimeout(timer.current);
      timer.current = setTimeout(refresh, 400);
    };
    es.addEventListener("end", () => {
      es.close();
      refresh();
    });
    return () => {
      es.close();
      clearTimeout(timer.current);
    };
  }, [id, refresh]);

  return { inv, insights, signals, report, events, error, refresh };
}

export const fmt = {
  pct: (x) => (x == null ? "—" : `${Math.round(x * 100)}%`),
  num: (x, d = 2) => (x == null ? "—" : typeof x === "number" ? (Number.isInteger(x) ? String(x) : x.toFixed(d)) : String(x)),
  date: (s) => (s ? String(s).slice(0, 10) : "n/a"),
  initials: (s) => (s || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase(),
};
