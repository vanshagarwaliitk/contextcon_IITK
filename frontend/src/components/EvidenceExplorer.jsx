import React, { useMemo, useState } from "react";
import { fmt } from "../api.js";

const TYPES = [
  ["all", "All"],
  ["hiring_cluster", "Postings"],
  ["leadership_change", "People"],
  ["web_evidence", "Web"],
  ["company_change", "Profile"],
  ["derived_metric", "Computed"],
];

export default function EvidenceExplorer({ signals, themes, highlight, clearHighlight }) {
  const [type, setType] = useState("all");
  const [theme, setTheme] = useState("all");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(30);
  const hl = new Set(highlight.ids);

  const list = useMemo(() => {
    let xs = signals.filter((s) => !(s.theme_tags || []).includes("baseline") || type === "derived_metric");
    if (hl.size) xs = xs.filter((s) => hl.has(s.id));
    if (type !== "all") xs = xs.filter((s) => s.signal_type === type);
    if (theme !== "all") xs = xs.filter((s) => (s.theme_tags || []).includes(theme));
    if (q.trim()) {
      const n = q.toLowerCase();
      xs = xs.filter((s) => `${s.title} ${s.summary} ${s.source_name}`.toLowerCase().includes(n));
    }
    return [...xs].sort((a, b) => (b.observed_at || "").localeCompare(a.observed_at || ""));
  }, [signals, type, theme, q, highlight]);

  const counts = Object.fromEntries(TYPES.map(([t]) => [t, t === "all" ? signals.length : signals.filter((s) => s.signal_type === t).length]));

  return (
    <>
      <div className="section-title" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 10 }}>
          <h2>Evidence explorer</h2>
          <span className="faint">every observation, with its source and retrieval date</span>
        </div>
      </div>
      {hl.size > 0 && (
        <div className="banner" style={{ background: "var(--accent-bg)", color: "var(--accent-ink)", marginTop: 0, marginBottom: 10 }}>
          Showing the {hl.size} signal{hl.size > 1 ? "s" : ""} behind: <b style={{ fontWeight: 600 }}>{highlight.label}</b>
          <span className="spacer" />
          <button className="small" onClick={clearHighlight}>Show everything</button>
        </div>
      )}
      <div className="filters">
        {TYPES.map(([t, label]) => (
          <button key={t} className={`chip ${type === t ? "on" : ""}`} onClick={() => setType(t)}>
            {label} <span style={{ opacity: 0.6 }}>{counts[t]}</span>
          </button>
        ))}
      </div>
      <div className="row" style={{ marginBottom: 6 }}>
        <select value={theme} onChange={(e) => setTheme(e.target.value)} style={{ maxWidth: 220 }}>
          <option value="all">All themes</option>
          {themes.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles, sources…" />
      </div>
      {list.length === 0 && <div className="empty">No signals match these filters.</div>}
      {list.slice(0, limit).map((s) => (
        <div key={s.id} className={`sig ${hl.has(s.id) ? "hl" : ""}`}>
          <div className={`stripe t-${s.signal_type}`} />
          <div style={{ minWidth: 0 }}>
            <div className="row wrap" style={{ gap: 6 }}>
              <span className="tag fact">Fact</span>
              <span style={{ fontWeight: 500 }}>{s.title}</span>
              {s.status === "weak" && <span className="pill warn">weak</span>}
            </div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{s.summary}</div>
            {s.computation && <div className="faint" style={{ marginTop: 2 }}>Computed: {s.computation}</div>}
            <div className="faint" style={{ marginTop: 3 }}>
              <span className="mono">{s.id}</span> · {s.signal_type.replaceAll("_", " ")} · observed {fmt.date(s.observed_at)} ·{" "}
              {s.source_url ? <a href={s.source_url} target="_blank" rel="noreferrer">{s.source_name}</a> : s.source_name} · retrieved{" "}
              {fmt.date(s.retrieved_at)} · {s.provider_mode}
            </div>
          </div>
        </div>
      ))}
      {list.length > limit && (
        <button className="small" style={{ marginTop: 10 }} onClick={() => setLimit(limit + 30)}>
          Show {Math.min(30, list.length - limit)} more of {list.length - limit}
        </button>
      )}
    </>
  );
}
