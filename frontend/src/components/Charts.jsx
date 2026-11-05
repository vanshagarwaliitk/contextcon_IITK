import React, { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const SERIES = ["#378ADD", "#D85A30", "#1D9E75", "#7F77DD", "#BA7517"];
const TIERS = ["junior", "mid", "senior", "lead", "leadership"];

/** Read theme colours from CSS variables and re-read when the light/dark theme flips. */
function useVars() {
  const read = () => {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    return { ink2: v("--ink-2"), ink3: v("--ink-3"), line: v("--line"), surface: v("--surface"), ink: v("--ink"), accent: v("--accent"), bad: v("--bad") };
  };
  const [vars, setVars] = useState(read);
  useEffect(() => {
    const mo = new MutationObserver(() => setVars(read()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return vars;
}

function Box({ title, sub, children, empty }) {
  return (
    <div className="chart-box">
      <h3>{title}</h3>
      <div className="faint" style={{ marginBottom: 8 }}>{sub}</div>
      {empty ? <div className="empty" style={{ padding: "30px 0" }}>{empty}</div> : <div style={{ height: 220 }}>{children}</div>}
    </div>
  );
}

export default function Charts({ insights, signals }) {
  const v = useVars();
  if (!insights) return null;
  const themes = insights.themes || [];
  const ledger = insights.ledger || {};
  const feats = insights.features || {};
  const W = insights.windows || {};
  const axis = { tick: { fill: v.ink2, fontSize: 12 }, axisLine: { stroke: v.line }, tickLine: false };
  const tip = { contentStyle: { background: v.surface, border: `1px solid ${v.line}`, borderRadius: 8, color: v.ink, fontSize: 12.5 }, cursor: { fill: v.line, opacity: 0.4 } };

  // 1. Hiring rate per 30 days, per theme, older -> prior -> recent (rate, not level, so windows compare fairly)
  const windows = [["older", "4–12 mo ago"], ["prior", "1–4 mo ago"], ["recent", "Last 30 days"]];
  const trend = windows.map(([w, label]) => {
    const row = { window: label };
    themes.forEach((t) => {
      const n = ledger[t.id]?.count?.[w];
      row[t.label] = n == null || !W[w]?.days ? null : Math.round((n / W[w].days) * 30 * 10) / 10;
    });
    return row;
  });
  const hasTrend = themes.some((t) => Object.keys(ledger[t.id]?.count || {}).length);

  // 2. Company vs peers share, for the first theme with peer data
  const peerTheme = themes.find((t) => (ledger[t.id]?.peers || []).length && feats[t.id]?.concentration_recent != null);
  const peers = peerTheme
    ? [
        { name: "This company", share: Math.round(feats[peerTheme.id].concentration_recent * 1000) / 10, self: true },
        ...ledger[peerTheme.id].peers.filter((p) => p.share != null).map((p) => ({ name: p.company, share: Math.round(p.share * 1000) / 10 })),
      ]
    : [];

  // 3. Seniority mix of fetched theme postings
  const hiring = signals.filter((s) => s.signal_type === "hiring_cluster");
  const seniority = TIERS.map((t) => ({ tier: t, postings: hiring.filter((s) => s.features?.seniority_level === t).length }));

  // 4. Geography of recent theme postings, new countries highlighted
  const geoTheme = themes.find((t) => (ledger[t.id]?.geo || []).length);
  const newC = new Set(geoTheme ? feats[geoTheme.id]?.geo_new_countries || [] : []);
  const geo = geoTheme ? ledger[geoTheme.id].geo.slice(0, 8).map((b) => ({ country: b.key || "unknown", postings: b.count, isNew: newC.has(b.key) })) : [];

  return (
    <div className="charts">
      <Box title="Theme hiring rate" sub="Postings per 30 days — change matters more than level" empty={!hasTrend && "No theme counts yet."}>
        <ResponsiveContainer>
          <BarChart data={trend} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={v.line} />
            <XAxis dataKey="window" {...axis} />
            <YAxis {...axis} allowDecimals />
            <Tooltip {...tip} />
            <Legend wrapperStyle={{ fontSize: 12, color: v.ink2 }} />
            {themes.map((t, i) => (
              <Bar key={t.id} dataKey={t.label} fill={SERIES[i % SERIES.length]} radius={[4, 4, 0, 0]} maxBarSize={34} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </Box>

      <Box
        title="Company vs peers"
        sub={peerTheme ? `Share of recent postings in “${peerTheme.label}” (%) — similar bars mean industry trend` : "Share of recent postings in the theme"}
        empty={!peers.length && "No competitor baseline — add a peer set to compare."}
      >
        <ResponsiveContainer>
          <BarChart data={peers} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={v.line} />
            <XAxis dataKey="name" {...axis} />
            <YAxis {...axis} unit="%" />
            <Tooltip {...tip} formatter={(x) => `${x}%`} />
            <Bar dataKey="share" radius={[4, 4, 0, 0]} maxBarSize={44}>
              {peers.map((p, i) => <Cell key={i} fill={p.self ? SERIES[0] : v.ink3} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Box>

      <Box title="Seniority mix" sub="Lead and leadership roles hint at a real build-out" empty={!hiring.length && "No postings fetched yet."}>
        <ResponsiveContainer>
          <BarChart data={seniority} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={v.line} />
            <XAxis dataKey="tier" {...axis} />
            <YAxis {...axis} allowDecimals={false} />
            <Tooltip {...tip} />
            <Bar dataKey="postings" radius={[4, 4, 0, 0]} maxBarSize={40}>
              {seniority.map((s, i) => <Cell key={i} fill={i >= 3 ? SERIES[2] : v.ink3} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Box>

      <Box
        title="Where they're hiring"
        sub={geoTheme ? `Recent “${geoTheme.label}” postings by country — new countries highlighted` : "Recent theme postings by country"}
        empty={!geo.length && "No geography probe yet."}
      >
        <ResponsiveContainer>
          <BarChart data={geo} layout="vertical" margin={{ top: 4, right: 12, left: 10, bottom: 0 }}>
            <CartesianGrid horizontal={false} stroke={v.line} />
            <XAxis type="number" {...axis} allowDecimals={false} />
            <YAxis type="category" dataKey="country" width={100} {...axis} />
            <Tooltip {...tip} formatter={(x, n, p) => [x, p.payload.isNew ? "postings (new country)" : "postings"]} />
            <Bar dataKey="postings" radius={[0, 4, 4, 0]} maxBarSize={18}>
              {geo.map((g, i) => <Cell key={i} fill={g.isNew ? SERIES[1] : SERIES[0]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Box>
    </div>
  );
}
