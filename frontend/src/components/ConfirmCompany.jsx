import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { ApiKeys } from "./Home.jsx";

export default function ConfirmCompany({ inv, onDone }) {
  const [domain, setDomain] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);
  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
  }, []);

  async function confirm(body) {
    setBusy(true);
    try {
      await api.confirm(inv.id, body);
      onDone();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" style={{ marginTop: 16, borderColor: "var(--warn)" }}>
      <h2>Which company did you mean?</h2>
      <p className="muted" style={{ margin: "6px 0 4px" }}>
        The match is ambiguous or name-only, so research is paused. Researching the wrong company would make every finding wrong.
      </p>
      {(inv.candidates || []).map((c) => (
        <div className="cand" key={c.crustdata_company_id}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>{c.name}</div>
            <div className="faint">
              {c.primary_domain || "no domain"} · id {c.crustdata_company_id}
              {c.basic_info?.employee_count_range ? ` · ${c.basic_info.employee_count_range} employees` : ""}
            </div>
          </div>
          <button className="small" disabled={busy} onClick={() => confirm({ crustdata_company_id: c.crustdata_company_id })}>
            This one
          </button>
        </div>
      ))}
      {(!inv.candidates || inv.candidates.length === 0) && <div className="faint">No candidates found.</div>}
      <form
        className="row"
        style={{ marginTop: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (!domain.trim()) return setErr("Enter a domain first.");
          confirm({ domain: domain.trim() });
        }}
      >
        <input value={domain} onChange={(e) => (setErr(""), setDomain(e.target.value))} placeholder="Correct domain, e.g. acme.com" />
        <button disabled={busy}>Use domain</button>
      </form>
      <details>
        <summary>API keys for the resumed run (needed again after a page reload)</summary>
        <ApiKeys health={health} onChange={() => setErr("")} compact />
      </details>
      {err && <div className="banner bad">{err}</div>}
    </section>
  );
}
