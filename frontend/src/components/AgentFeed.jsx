import React, { useEffect, useRef, useState } from "react";

const CLAUDE_STAGES = new Set(["scoping", "planner", "cluster", "hypothesize", "falsify", "verify", "brief"]);
const DATA_STAGES = new Set(["resolve", "baseline", "probe"]);

/** Turn raw pipeline events into readable agent steps. */
function describe(e) {
  const m = e.message;
  if (e.level === "error") return { kind: "warn", icon: "!", text: m === "traceback" ? `Error in ${e.stage}` : m };
  if (e.level === "warn") return { kind: "warn", icon: "!", text: m };
  if (/^Stage \w+ started$/.test(m)) return null;
  if (/^Stage \w+ done$/.test(m)) return { kind: "good", icon: "✓", text: `${e.stage} finished`, quiet: true };
  if (e.stage === "planner" && m.startsWith("decide ")) {
    const [call, reason] = m.slice(7).split(" — ");
    return { kind: "claude", icon: "AI", text: <>Claude chose <code>{call}</code></>, sub: reason };
  }
  if (e.stage === "planner" && m.startsWith("stop")) return { kind: "claude", icon: "AI", text: "Claude stopped probing", sub: m.split(" — ")[1] };
  if (e.stage === "probe") {
    const [call, reason] = m.split(" — ");
    return { kind: "data", icon: "CD", text: <>Crustdata <code>{call}</code></>, sub: reason };
  }
  if (CLAUDE_STAGES.has(e.stage)) return { kind: "claude", icon: "AI", text: m };
  if (DATA_STAGES.has(e.stage)) return { kind: "data", icon: "CD", text: m };
  return { kind: "", icon: "•", text: m };
}

export default function AgentFeed({ events, running, budget }) {
  const [showAll, setShowAll] = useState(false);
  const box = useRef(null);
  const items = events.map((e) => ({ e, d: describe(e) })).filter((x) => x.d && (showAll || !x.d.quiet));

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [items.length]);

  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h2>Agent activity</h2>
        {running ? (
          <span className="pill accent">
            <span className="spin" /> live
          </span>
        ) : (
          <span className="faint">{events.length} events</span>
        )}
      </div>
      <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>
        <span className="pill violet">AI Claude decision</span>
        <span className="pill accent">CD Crustdata call</span>
        <label className="faint row" style={{ gap: 4, marginLeft: "auto", cursor: "pointer" }}>
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} style={{ width: "auto" }} />
          all steps
        </label>
      </div>
      <div className="feed" ref={box}>
        {items.length === 0 && <div className="faint">Waiting for the first step…</div>}
        {items.map(({ e, d }) => (
          <div className="feed-item" key={e.id}>
            <div className={`feed-icon ${d.kind}`}>{d.icon}</div>
            <div style={{ minWidth: 0 }}>
              <div style={{ wordBreak: "break-word" }}>{d.text}</div>
              {d.sub && <div className="feed-meta">{d.sub}</div>}
              <div className="feed-meta">
                {e.stage} · {String(e.created_at).slice(11, 19)}
              </div>
            </div>
          </div>
        ))}
      </div>
      {budget && (
        <div className="faint" style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
          {budget.calls ?? 0}/{budget.max_calls ?? "—"} API calls · {budget.credits ?? 0}/{budget.max_credits ?? "—"} est. credits
          {budget.unverified_cost_calls ? ` · ${budget.unverified_cost_calls} aggregation calls with unverified cost` : ""}
        </div>
      )}
    </section>
  );
}
