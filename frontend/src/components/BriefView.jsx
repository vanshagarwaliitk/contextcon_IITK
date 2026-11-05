import React, { useState } from "react";
import { fmt } from "../api.js";

const SECTIONS = [
  ["executive_summary", "Executive summary"],
  ["key_findings", "Key findings"],
  ["hypotheses", "Hypotheses"],
  ["counter_evidence_and_alternatives", "Counter-evidence and alternatives"],
  ["unknowns", "Unknowns"],
  ["recommended_next_indicators", "Recommended next indicators"],
];

function tagFor(c) {
  if (!c) return ["unv", "Note", ""];
  if (c.status === "unverified") return ["unv", "Not verified", "unverified"];
  if (c.claim_type === "fact") return ["fact", "Fact", "fact"];
  if (c.claim_type === "hypothesis" || c.claim_type === "hypothesis_flagged") return ["hyp", "Hypothesis", "hypothesis"];
  return ["rec", "Recommendation", "recommendation"];
}

export default function BriefView({ report, invId, onShowSignals }) {
  const [sel, setSel] = useState(null);
  const claims = Object.fromEntries(report.claims.map((c) => [c.id, c]));
  const S = report.sections;

  return (
    <>
      <div className="section-title" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 10 }}>
          <h2>Intelligence brief</h2>
          <span className="faint">click any sentence to see the exact signals behind it</span>
        </div>
        <span className="faint">
          coverage {fmt.pct(report.coverage?.evidence_coverage)} · writer {S.writer}
        </span>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <span className="tag fact">Fact</span><span className="faint">observed, sourced</span>
        <span className="tag hyp">Hypothesis</span><span className="faint">interpretation</span>
        <span className="tag rec">Recommendation</span><span className="faint">what to watch</span>
        <span className="tag unv">Not verified</span><span className="faint">no usable source</span>
      </div>
      <div className="brief">
        {SECTIONS.map(([key, title]) => (
          <div key={key}>
            <h3>{title}</h3>
            {(S[key] || []).length === 0 && <div className="faint" style={{ fontFamily: "var(--font)" }}>Nothing to report.</div>}
            {(S[key] || []).map((s, i) => {
              const c = claims[(s.claim_ids || [])[0]];
              const [tcls, tlabel, ccls] = tagFor(c);
              const sigIds = [...new Set((s.claim_ids || []).flatMap((id) => claims[id]?.signal_ids || []))];
              const k = `${key}-${i}`;
              return (
                <div
                  key={k}
                  className={`claim ${ccls} ${sel === k ? "sel" : ""}`}
                  onClick={() => {
                    setSel(k);
                    if (sigIds.length) onShowSignals(sigIds, s.text.slice(0, 80) + (s.text.length > 80 ? "…" : ""));
                  }}
                  title={c?.verdict_reason ? `Verifier: ${c.verdict} — ${c.verdict_reason}` : undefined}
                >
                  <span className={`tag ${tcls}`} style={{ fontFamily: "var(--font)", marginRight: 8 }}>{tlabel}</span>
                  {s.text}
                  <span className="ids">
                    {(s.claim_ids || []).join(", ")}
                    {sigIds.length ? ` → ${sigIds.length} signal${sigIds.length > 1 ? "s" : ""}` : " · no source"}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <p className="faint" style={{ marginTop: 18 }}>{report.status_note}</p>
    </>
  );
}
