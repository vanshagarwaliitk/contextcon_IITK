import React, { useEffect, useState } from "react";
import { api, fmt, keyStore } from "../api.js";

/** Provider keys for this run. Held in memory only; blank fields use the server's .env keys. */
export function ApiKeys({ health, onChange, compact }) {
  const [keys, setKeys] = useState({
    anthropic_api_key: keyStore.anthropic_api_key,
    crustdata_api_key: keyStore.crustdata_api_key,
  });
  const live = !health || health.provider_mode === "live";
  const update = (k) => (e) => {
    const next = { ...keys, [k]: e.target.value };
    setKeys(next);
    keyStore.set(next);
    onChange?.();
  };
  const envHint = (has) => (has ? "Optional — the server .env has one" : live ? "Required — no key in the server .env" : "Not needed in replay mode");
  const account = health?.claude_account && health?.llm_backend !== "api";
  const anthropicHint = health?.anthropic_key
    ? envHint(true)
    : account
      ? "Optional — leave empty to use your Claude account"
      : envHint(false);
  return (
    <div style={{ marginTop: compact ? 10 : 14, paddingTop: compact ? 0 : 12, borderTop: compact ? 0 : "1px solid var(--line)" }}>
      {account && !health?.anthropic_key && (
        <div className="banner" style={{ background: "var(--violet-bg)", color: "var(--violet)", marginTop: 0, marginBottom: 10 }}>
          Claude runs through your logged-in Claude account (Claude Code on this computer). No Anthropic key needed.
          Usage counts against your Claude plan.
        </div>
      )}
      <div className="form-grid">
        <label className="field">
          Anthropic API key <span className="faint">· {anthropicHint}</span>
          <input type="password" autoComplete="off" spellCheck="false" value={keys.anthropic_api_key}
                 onChange={update("anthropic_api_key")} placeholder="sk-ant-…" />
        </label>
        <label className="field">
          Crustdata API key <span className="faint">· {envHint(health?.crustdata_key)}</span>
          <input type="password" autoComplete="off" spellCheck="false" value={keys.crustdata_api_key}
                 onChange={update("crustdata_api_key")} placeholder="Your Crustdata key" />
        </label>
      </div>
      <div className="faint" style={{ marginTop: 6 }}>
        Keys go to this server for this investigation only. They aren't saved to the database or browser storage. Use HTTPS when hosting.
      </div>
    </div>
  );
}

const EXAMPLES = [
  "Are they moving into robotics?",
  "Are they expanding into Europe?",
  "Are they building an AI product team?",
  "Are they moving upmarket to enterprise?",
];

const STATUS_PILL = {
  completed: "good",
  completed_with_warnings: "warn",
  running: "accent",
  needs_confirmation: "warn",
  failed: "bad",
};

export default function Home({ health }) {
  const [form, setForm] = useState({ domain: "", company_name: "", user_question: "", competitors: "" });
  const [recent, setRecent] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.list().then(setRecent).catch(() => {});
  }, []);

  const set = (k) => (e) => {
    setErr("");
    setForm({ ...form, [k]: e.target.value });
  };

  async function start(e) {
    e.preventDefault();
    if (!form.domain.trim() && !form.company_name.trim()) {
      setErr("Enter a competitor domain or company name.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.create({
        domain: form.domain.trim() || null,
        company_name: form.company_name.trim() || null,
        user_question: form.user_question.trim() || null,
        competitors: form.competitors.split(",").map((s) => s.trim()).filter(Boolean),
      });
      window.location.hash = `inv/${r.id}`;
    } catch (ex) {
      setErr(String(ex.message || ex));
      setBusy(false);
    }
  }

  return (
    <>
      <section className="hero">
        <h1>What is your competitor preparing to do?</h1>
        <p>
          CoreData reads public hiring, people and web signals from Crustdata, connects them, tries to disprove its own
          hypotheses, and hands you a sourced brief.
        </p>
      </section>

      <form className="card" onSubmit={start} style={{ maxWidth: 820, margin: "0 auto" }}>
        <div className="form-grid">
          <label className="field">
            Competitor domain
            <input value={form.domain} onChange={set("domain")} placeholder="acme.com" />
          </label>
          <label className="field">
            Or company name
            <input value={form.company_name} onChange={set("company_name")} placeholder="Acme Robotics" />
          </label>
        </div>
        <label className="field" style={{ marginTop: 12 }}>
          Question (optional — leave empty for a data-driven scan)
          <input value={form.user_question} onChange={set("user_question")} placeholder="Are they moving into robotics?" />
        </label>
        <div className="row wrap" style={{ marginTop: 8, gap: 6 }}>
          {EXAMPLES.map((q) => (
            <button type="button" key={q} className="chip" onClick={() => setForm({ ...form, user_question: q })}>
              {q}
            </button>
          ))}
        </div>
        <label className="field" style={{ marginTop: 12 }}>
          Peer set for contrast (comma-separated domains or names)
          <input value={form.competitors} onChange={set("competitors")} placeholder="peer1.com, peer2.com" />
        </label>
        <ApiKeys health={health} onChange={() => setErr("")} />
        {err && <div className="banner bad">{err}</div>}
        <div className="row" style={{ marginTop: 16 }}>
          <button className="primary" disabled={busy}>
            {busy ? "Starting…" : "Start investigation"}
          </button>
        </div>
      </form>

      <section style={{ maxWidth: 820, margin: "28px auto 0" }}>
        <div className="section-title">
          <h2>Recent investigations</h2>
        </div>
        {recent.length === 0 ? (
          <div className="faint">Your investigations will show up here.</div>
        ) : (
          <div className="recent">
            {recent.map((r) => (
              <div className="recent-item" key={r.id} onClick={() => (window.location.hash = `inv/${r.id}`)}>
                <div className="avatar" style={{ width: 34, height: 34, fontSize: 12 }}>
                  {fmt.initials(r.resolved_name || r.company_name || r.domain)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500 }}>{r.resolved_name || r.company_name || r.domain}</div>
                  <div className="faint">{r.user_question || "Data-driven scan"} · {fmt.date(r.created_at)}</div>
                </div>
                <span className={`pill ${STATUS_PILL[r.status] || ""}`}>{r.status.replaceAll("_", " ")}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
