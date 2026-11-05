import React from "react";
import { fmt } from "../api.js";

export const STAGES = ["resolve", "baseline", "scoping", "probes", "features", "cluster", "hypothesize", "falsify", "score", "verify", "brief"];
const STAGE_LABEL = {
  resolve: "Resolve", baseline: "Baseline", scoping: "Themes", probes: "Probes", features: "Features",
  cluster: "Cluster", hypothesize: "Hypotheses", falsify: "Falsify", score: "Score", verify: "Verify", brief: "Brief",
};
const RANK = { High: 3, Medium: 2, Low: 1 };

export default function VerdictHeader({ inv, insights, report, signals }) {
  const hyps = report?.hypotheses || [];
  const top = [...hyps].sort((a, b) => (RANK[b.evidence_strength] || 0) - (RANK[a.evidence_strength] || 0))[0];
  const budget = inv.budget || {};
  const maxCredits = budget.max_credits || insights?.budget?.max_credits;
  const running = inv.running || inv.status === "running";
  const active = running ? STAGES.find((s) => !inv.stages[s]) : null;
  const probes = (insights && Object.values(insights.ledger || {}).length) || 0;
  const name = inv.resolved_name || inv.company_name || inv.domain;

  return (
    <section className="card">
      <div className="row wrap" style={{ justifyContent: "space-between", gap: 14 }}>
        <div className="row" style={{ gap: 14, minWidth: 0 }}>
          <div className="avatar">{fmt.initials(name)}</div>
          <div style={{ minWidth: 0 }}>
            <h1 style={{ fontSize: 22 }}>{name}</h1>
            <div className="muted">
              {inv.resolved_domain || inv.domain || "domain pending"}
              {inv.resolved_company_id != null && <span className="faint"> · Crustdata id {inv.resolved_company_id}</span>}
            </div>
            <div style={{ marginTop: 4, fontFamily: "var(--serif)", fontSize: 15.5 }}>
              {inv.user_question ? `“${inv.user_question}”` : <span className="muted">Data-driven scan (no question)</span>}
            </div>
          </div>
        </div>
        <div className="row wrap">
          <StatusPill status={inv.status} running={running} />
          {inv.provider_mode !== "live" && <span className="pill bad">Replay data</span>}
          {report && (
            <>
              <a href={`/api/investigations/${inv.id}/brief`} target="_blank" rel="noreferrer">
                <button className="small">Printable brief</button>
              </a>
              <a href={`/api/investigations/${inv.id}/export`}>
                <button className="small primary">Export</button>
              </a>
            </>
          )}
        </div>
      </div>

      {inv.provider_mode !== "live" && (
        <div className="banner replay">Contains replayed data — recorded real Crustdata responses, not a live pull.</div>
      )}

      <div className="metrics">
        <div className="metric">
          <div className="label">Top hypothesis</div>
          <div className={`value ${top?.evidence_strength || ""}`}>
            {top ? top.evidence_strength : report ? (report.outcome === "insufficient_evidence" ? "Insufficient" : "None") : "—"}
          </div>
          {top && <div className="faint">{(top.evidence_status || "").replaceAll("_", " ")}</div>}
        </div>
        <div className="metric">
          <div className="label">Evidence coverage</div>
          <div className="value">{fmt.pct(inv.coverage?.evidence_coverage)}</div>
          <div className="faint">facts with a cited source</div>
        </div>
        <div className="metric">
          <div className="label">Signals collected</div>
          <div className="value">{signals.filter((s) => !s.derived).length}</div>
          <div className="faint">+ {signals.filter((s) => s.derived).length} computed metrics</div>
        </div>
        <div className="metric">
          <div className="label">Crustdata spend</div>
          <div className="value">
            {fmt.num(budget.credits ?? 0, 1)}
            <span className="faint" style={{ fontSize: 13 }}> / {maxCredits ?? "—"} credits</span>
          </div>
          <div className="meter">
            <div style={{ width: `${maxCredits ? Math.min(100, ((budget.credits || 0) / maxCredits) * 100) : 0}%` }} />
          </div>
          <div className="faint" style={{ marginTop: 3 }}>{budget.calls ?? 0} API calls{probes ? ` · ${probes} themes probed` : ""}</div>
        </div>
      </div>

      <div className="stages" aria-label="Pipeline stages">
        {STAGES.map((s) => {
          const st = inv.stages[s];
          const cls = st === "done" ? "done" : st === "failed" ? "failed" : s === active ? "active" : "";
          return (
            <span key={s} className={`stage ${cls}`}>
              {cls === "active" && <span className="spin" />}
              {st === "done" && "✓"}
              {st === "failed" && "✕"}
              {STAGE_LABEL[s]}
            </span>
          );
        })}
      </div>
      {inv.warnings?.length > 0 && (
        <details>
          <summary>
            {inv.warnings.length} warning{inv.warnings.length > 1 ? "s" : ""} — the run degraded gracefully
          </summary>
          <ul className="faint" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {inv.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function StatusPill({ status, running }) {
  const map = {
    completed: ["good", "Completed"],
    completed_with_warnings: ["warn", "Completed with warnings"],
    needs_confirmation: ["warn", "Needs confirmation"],
    failed: ["bad", "Failed"],
    created: ["", "Queued"],
    running: ["accent", "Running"],
  };
  const [cls, label] = running ? ["accent", "Running"] : map[status] || ["", status];
  return (
    <span className={`pill ${cls}`}>
      {running && <span className="spin" />} {label}
    </span>
  );
}
