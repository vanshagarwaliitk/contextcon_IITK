import React, { useState } from "react";
import { api } from "../api.js";

const TEST_LABEL = {
  novelty_test: "Novelty", evergreen_test: "Not evergreen", scale_test: "Scale", peer_test: "Beats peers",
  seniority_test: "Seniority", geo_test: "New geography", corroboration_test: "Primary source",
  source_diversity_test: "Source diversity", disconfirm_web: "Disconfirming search",
};
const TYPE_LABEL = (t) => (t || "").replaceAll("_", " ");

function SignalLine({ s, id }) {
  if (!s) return <div className="ev"><span className="id">{id}</span><span className="faint">signal not found</span></div>;
  return (
    <div className="ev">
      <span className="id">{s.id}</span>
      <span style={{ minWidth: 0 }}>
        {s.derived ? s.summary : s.title}
        <span className="faint">
          {" "}· {s.derived ? "computed" : s.signal_type.replaceAll("_", " ")}
          {s.source_url ? (
            <> · <a href={s.source_url} target="_blank" rel="noreferrer">{s.source_name}</a></>
          ) : s.source_name && !s.derived ? ` · ${s.source_name}` : ""}
        </span>
      </span>
    </div>
  );
}

export default function HypothesisCard({ h, sigById, invId, onShowSignals }) {
  const [more, setMore] = useState(false);
  const [watched, setWatched] = useState(false);
  const bd = h.score_breakdown || {};
  const strength = h.evidence_strength || "Low";
  const width = Math.max(6, Math.min(100, (((bd.points ?? 0) + 2) / 14) * 100));

  const support = h.supporting_signal_ids || [];
  const evidence = support.filter((i) => sigById[i] && !sigById[i].derived);
  const metrics = support.filter((i) => sigById[i]?.derived);
  const shownFor = more ? [...evidence, ...metrics] : [...evidence.slice(0, 4), ...metrics.slice(0, evidence.length ? 1 : 3)];
  const failed = (h.tests || []).filter((t) => t.outcome === "contradicts");
  const contra = h.contradicting_signal_ids || [];
  const insufficient = h.hypothesis_type === "insufficient_evidence";

  return (
    <article className="hyp-card">
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 6 }}>
          <span className="tag hyp">Hypothesis</span>
          <span className="faint">{TYPE_LABEL(h.hypothesis_type)}</span>
        </div>
        <span className={`pill ${h.evidence_status === "corroborated" ? "good" : h.evidence_status === "conflicting" ? "warn" : ""}`}>
          {(h.evidence_status || "unverified").replaceAll("_", " ")}
        </span>
      </div>
      <div className="hyp-title">{h.title}</div>
      <div className="hyp-statement">{h.explanation}</div>

      <div className="strength">
        <span className="faint">Strength</span>
        <div className="bar"><div className={`fill-${strength}`} style={{ width: `${width}%` }} /></div>
        <span className={strength} style={{ fontWeight: 600 }}>
          {strength} <span className="faint">· {bd.points ?? 0} pts</span>
        </span>
      </div>

      {!insufficient && (
        <div className="tests">
          {(h.tests || []).map((t) => (
            <span
              key={t.id}
              title={t.explanation}
              className={`pill ${t.outcome === "supports" ? "good" : t.outcome === "contradicts" ? "bad" : ""}`}
            >
              {t.outcome === "supports" ? "✓" : t.outcome === "contradicts" ? "✕" : "–"} {TEST_LABEL[t.test_id] || t.test_id}
            </span>
          ))}
        </div>
      )}

      <div className="cols">
        <div>
          <div className="col-head for">For · {support.length}</div>
          {shownFor.map((id) => <SignalLine key={id} id={id} s={sigById[id]} />)}
          {support.length === 0 && <div className="faint" style={{ paddingTop: 8 }}>No supporting signals.</div>}
          {support.length > shownFor.length && (
            <button className="ghost small" onClick={() => setMore(true)}>+ {support.length - shownFor.length} more</button>
          )}
        </div>
        <div>
          <div className="col-head against">Against and alternatives · {contra.length + (h.alternatives || []).length}</div>
          {failed.map((t) => (
            <div className="ev" key={t.id}>
              <span className="id neg">test</span>
              <span>{t.explanation}</span>
            </div>
          ))}
          {contra.filter((id) => !failed.some((t) => (t.evidence_signal_ids || []).includes(id))).slice(0, 3).map((id) => (
            <SignalLine key={id} id={id} s={sigById[id]} />
          ))}
          {(h.alternatives || []).map((a) => (
            <div className="ev" key={a.ref}>
              <span className="id">{a.ref}</span>
              <span>
                {a.statement}
                {a.would_be_supported_by && <span className="faint"> — would show up as: {a.would_be_supported_by}</span>}
              </span>
            </div>
          ))}
          {contra.length + (h.alternatives || []).length === 0 && <div className="faint" style={{ paddingTop: 8 }}>None found.</div>}
        </div>
      </div>

      {h.diagnostic_note && <p className="faint" style={{ margin: "12px 0 0" }}>{h.diagnostic_note}</p>}

      <details>
        <summary>Why {strength}? Score breakdown</summary>
        <table className="breakdown">
          <tbody>
            {(bd.rules || []).map((r) => (
              <tr key={r.rule}>
                <td>{r.rule.replaceAll("_", " ")}</td>
                <td className="faint">{r.reason}</td>
                <td className={`pts ${r.points > 0 ? "pos" : r.points < 0 ? "neg" : ""}`}>{r.points > 0 ? `+${r.points}` : r.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {bd.caps_applied?.length > 0 && <div className="faint" style={{ marginTop: 6 }}>Capped: {bd.caps_applied.join("; ")}</div>}
      </details>

      {h.matrix?.length > 0 && (
        <details>
          <summary>Evidence matrix — which signals actually discriminate</summary>
          <Matrix matrix={h.matrix} sigById={sigById} />
        </details>
      )}

      {(h.evidence_gaps?.length > 0 || h.recommended_next_indicators?.length > 0) && (
        <div className="cols">
          <div>
            <div className="col-head">Evidence gaps</div>
            {h.evidence_gaps.map((g, i) => <div className="ev" key={i}><span className="tag unv">Unknown</span><span>{g}</span></div>)}
          </div>
          <div>
            <div className="col-head">Watch next</div>
            {h.recommended_next_indicators.map((g, i) => <div className="ev" key={i}><span className="tag rec">Next</span><span>{g}</span></div>)}
          </div>
        </div>
      )}

      <div className="row" style={{ marginTop: 14 }}>
        <button className="small" onClick={() => onShowSignals([...support, ...contra], h.title)}>Show all evidence</button>
        <button
          className="small ghost"
          disabled={watched}
          onClick={() => api.watch(invId, { item_type: "hypothesis", item_id: h.id }).then(() => setWatched(true))}
        >
          {watched ? "On watchlist" : "Add to watchlist"}
        </button>
      </div>
    </article>
  );
}

function Matrix({ matrix, sigById }) {
  const ids = [...new Set(matrix.map((m) => m.signal_id))];
  const cell = (id, ref) => matrix.find((m) => m.signal_id === id && m.hypothesis_ref === ref)?.relation || "neutral";
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="matrix">
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Signal</th>
            <th>H1 main</th>
            <th>H2 benign</th>
            <th>H3 noise</th>
            <th>Diagnostic?</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((id) => {
            const rels = ["H1", "H2", "H3"].map((r) => cell(id, r));
            const diagnostic = new Set(rels).size > 1;
            return (
              <tr key={id}>
                <td style={{ textAlign: "left", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <span className="mono faint">{id}</span> {sigById[id]?.title || ""}
                </td>
                {rels.map((r, i) => <td key={i} className={`rel-${r}`}>{r}</td>)}
                <td className={diagnostic ? "pos" : "faint"}>{diagnostic ? "yes" : "no"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
