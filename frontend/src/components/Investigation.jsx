import React, { useMemo, useRef, useState } from "react";
import { DONE, useInvestigation } from "../api.js";
import VerdictHeader from "./VerdictHeader.jsx";
import AgentFeed from "./AgentFeed.jsx";
import ConfirmCompany from "./ConfirmCompany.jsx";
import HypothesisCard from "./HypothesisCard.jsx";
import Charts from "./Charts.jsx";
import EvidenceExplorer from "./EvidenceExplorer.jsx";
import BriefView from "./BriefView.jsx";

export default function Investigation({ id }) {
  const { inv, insights, signals, report, events, error, refresh } = useInvestigation(id);
  const [highlight, setHighlight] = useState({ ids: [], label: "" });
  const explorerRef = useRef(null);
  const sigById = useMemo(() => Object.fromEntries((report?.signals || signals).map((s) => [s.id, s])), [report, signals]);

  const showSignals = (ids, label) => {
    setHighlight({ ids, label });
    setTimeout(() => explorerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  if (error && !inv) return <div className="card banner bad">Couldn't load this investigation: {error}</div>;
  if (!inv)
    return (
      <div className="card">
        <div className="skeleton" style={{ width: "40%" }} />
        <div className="skeleton" style={{ width: "70%" }} />
      </div>
    );

  const done = DONE.includes(inv.status);
  const hyps = report?.hypotheses || [];

  return (
    <>
      <VerdictHeader inv={inv} insights={insights} report={report} signals={signals} />
      {inv.status === "needs_confirmation" && <ConfirmCompany inv={inv} onDone={refresh} />}
      {inv.status === "failed" && <div className="banner bad">The investigation failed: {inv.error}</div>}

      <div className="grid-main">
        <div style={{ minWidth: 0 }}>
          <section className="card">
            <div className="section-title">
              <h2>Hypotheses</h2>
              <span className="faint">interpretations of public signals — never facts or predictions</span>
            </div>
            {!done ? (
              <div className="empty">
                <div className="big">{inv.status === "needs_confirmation" ? "Waiting for you" : "Investigating…"}</div>
                {inv.status === "needs_confirmation"
                  ? "Confirm the company above to start research."
                  : "Hypotheses appear once signals are collected, clustered and stress-tested."}
              </div>
            ) : hyps.length === 0 ? (
              <InsufficientEvidence report={report} />
            ) : (
              hyps.map((h) => <HypothesisCard key={h.id} h={h} sigById={sigById} invId={id} onShowSignals={showSignals} />)
            )}
          </section>

          <section style={{ marginTop: 16 }}>
            <Charts insights={insights} signals={signals} />
          </section>

          {done && report && (
            <section className="card" style={{ marginTop: 16 }}>
              <BriefView report={report} invId={id} onShowSignals={showSignals} />
            </section>
          )}

          <section className="card" style={{ marginTop: 16 }} ref={explorerRef}>
            <EvidenceExplorer
              signals={report?.signals || signals}
              themes={insights?.themes || []}
              highlight={highlight}
              clearHighlight={() => setHighlight({ ids: [], label: "" })}
            />
          </section>
        </div>

        <aside style={{ position: "sticky", top: 70 }}>
          <AgentFeed events={events} running={inv.running || inv.status === "running"} budget={inv.budget} />
        </aside>
      </div>
    </>
  );
}

function InsufficientEvidence({ report }) {
  const llmDown = report?.sections?.writer === "templated" && report?.warnings?.some((w) => /LLM|unavailable/i.test(w));
  return (
    <div className="empty">
      <div className="big">{llmDown ? "Facts only — reasoning was unavailable" : "Insufficient evidence"}</div>
      <p style={{ maxWidth: 520, margin: "0 auto" }}>
        {llmDown
          ? "Claude couldn't be reached, so this run lists observed facts and computed features without strategic hypotheses."
          : "No signal cluster reached the bar (at least 3 related postings, or 2 different signal types). That's an honest result, not a failure."}
      </p>
      <p className="faint" style={{ marginTop: 10 }}>
        What would change this: more theme postings in the recent window, a senior hire in the theme, or a primary-source
        announcement on the company's own site.
      </p>
    </div>
  );
}
