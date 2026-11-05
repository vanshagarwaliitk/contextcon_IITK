"""Stage 10a — claim ledger + verifier (LLM judge B6 + deterministic checks)."""
from __future__ import annotations

from ..llm.client import LLMError
from ..llm.schemas import VerifyResult
from ..models import Signal, SignalType
from . import guards
from .context import Ctx
from .hypothesize import load_hypotheses

FEATURE_FACTS = ["theme_postings_recent", "theme_postings_prior", "theme_postings_older", "velocity_ratio",
                 "concentration_recent", "concentration_older", "seniority_share", "geo_new_countries",
                 "evergreen_ratio", "source_platforms", "peer_share_gap", "tech_postings_recent"]
MAX_POSTING_FACTS_PER_THEME = 8


class Ledger:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.claims: list[dict] = []

    def add(self, text: str, claim_type: str, signal_ids: list[str], section: str, verbatim: bool = False) -> dict:
        c = {"id": self.ctx.db.next_id(self.ctx.inv_id, "clm"), "text": text, "claim_type": claim_type,
             "signal_ids": list(dict.fromkeys(signal_ids)), "section": section, "verbatim": verbatim,
             "status": None, "verdict": None, "verdict_reason": None}
        self.claims.append(c)
        return c


def build_ledger(ctx: Ctx, signals: dict[str, Signal]) -> Ledger:
    L = Ledger(ctx)
    # ---- facts: profile, baseline totals, theme features, people, web, sample postings (verbatim signal summaries)
    for s in signals.values():
        if s.signal_type == SignalType.company_change:
            L.add(s.summary, "fact", [s.id], "company", verbatim=True)
    for w in ("recent", "prior", "older"):
        b = ctx.state.get("baseline", {}).get(f"{w}:count") or {}
        if b.get("signal_id") in signals:
            L.add(signals[b["signal_id"]].summary, "fact", [b["signal_id"]], "baseline", verbatim=True)
    feats = ctx.state.get("features", {})
    for theme in ctx.themes():
        ids = feats.get(theme.id, {}).get("_ids", {})
        for k in FEATURE_FACTS:
            if k in ids and ids[k] in signals:
                L.add(signals[ids[k]].summary, "fact", [ids[k]], f"theme:{theme.id}", verbatim=True)
        postings = [s for s in signals.values() if s.signal_type == SignalType.hiring_cluster and theme.id in s.theme_tags]
        postings.sort(key=lambda s: s.observed_at or "", reverse=True)
        for s in postings[:MAX_POSTING_FACTS_PER_THEME]:
            L.add(s.summary, "fact", [s.id], f"timeline:{theme.id}", verbatim=True)
        for s in signals.values():
            if theme.id in s.theme_tags and s.signal_type in (SignalType.leadership_change, SignalType.web_evidence):
                L.add(s.summary, "fact", [s.id], f"timeline:{theme.id}", verbatim=True)
    # ---- hypotheses, alternatives, gaps, recommendations
    for h in load_hypotheses(ctx):
        sup = h["supporting_signal_ids"]
        L.add(f"{h['title']}: {h['explanation']}", "hypothesis", sup, f"hypothesis:{h['id']}")
        for a in h["alternatives"]:
            L.add(f"Alternative ({a['ref']}): {a['statement']}", "hypothesis", a.get("consistent_signal_ids", []),
                  f"alternative:{h['id']}")
        for g in h["evidence_gaps"]:
            L.add(g, "fact", [], f"unknowns:{h['id']}")  # no source -> unverified
        for r in h["recommended_next_indicators"]:
            L.add(f"Monitor: {r}", "recommendation", sup, f"recommendation:{h['id']}")
    # ---- explicit unknowns from failures
    for w in ctx.warnings:
        if "failed" in w or "unavailable" in w:
            L.add(f"Data gap: {w}", "fact", [], "unknowns:pipeline")
    if ctx.state.get("outcome") == "insufficient_evidence":
        L.add("No signal cluster met the minimum evidence bar (>=3 postings or >=2 signal types).", "fact",
              [], "unknowns:outcome")
    return L


def deterministic_checks(c: dict, signals: dict[str, Signal]) -> list[str]:
    issues = []
    if guards.banned_in(c["text"]):
        issues.append(f"banned phrase {guards.banned_in(c['text'])}")
    evidence = [signals[i].summary + " " + signals[i].title + " " + str(signals[i].features.get("value", ""))
                for i in c["signal_ids"] if i in signals]
    if c["claim_type"] == "fact" and c["signal_ids"]:
        bad = guards.ungrounded(c["text"], guards.allowed_numbers(*evidence))
        if bad:
            issues.append(f"ungrounded numbers {bad}")
    return issues


def run(ctx: Ctx) -> dict:
    signals = ctx.signals()
    L = build_ledger(ctx, signals)
    to_judge = []
    for c in L.claims:
        issues = deterministic_checks(c, signals)
        if c["claim_type"] == "fact":
            if not c["signal_ids"]:
                c["status"], c["verdict"], c["verdict_reason"] = "unverified", "unsupported", "no source found"
            elif issues:
                c["status"], c["verdict"], c["verdict_reason"] = "unverified", "unsupported", "; ".join(issues)
            elif c["verbatim"]:
                c["status"], c["verdict"], c["verdict_reason"] = "verified", "supported", "verbatim transcription of stored signal"
            else:
                to_judge.append(c)
        else:
            c["status"] = c["claim_type"]
            if issues:
                c["verdict"], c["verdict_reason"] = "partial", "; ".join(issues)
            to_judge.append(c)

    if to_judge:
        payload = "\n\n".join(
            f"[{c['id']}] type={c['claim_type']}\nCLAIM: {c['text']}\nEVIDENCE:\n" +
            ("\n".join(f"  {i}: {signals[i].summary}" for i in c["signal_ids"] if i in signals) or "  (none)")
            for c in to_judge
        )
        try:
            res = ctx.llm.structured("b6_verify.md", {"claims_with_evidence": payload}, VerifyResult, effort="low")
            verdicts = {v.claim_id: v for v in res.verdicts}
            for c in to_judge:
                v = verdicts.get(c["id"])
                if v is None:
                    continue
                if c["verdict"] == "partial" and v.verdict == "supported":
                    continue  # keep deterministic flag
                c["verdict"], c["verdict_reason"] = v.verdict, v.reason[:200]
                if c["claim_type"] == "fact":
                    c["status"] = "verified" if v.verdict in ("supported", "partial") else "unverified"
        except LLMError as exc:
            ctx.warn("verify", f"LLM verifier unavailable ({exc}); only deterministic checks applied")
            for c in to_judge:
                if c["claim_type"] == "fact" and c["status"] is None:
                    c["status"] = "unverified"

    for c in L.claims:
        if c["claim_type"] in ("hypothesis", "recommendation") and c["verdict"] in ("unsupported", "contradicted"):
            c["status"] = f"{c['claim_type']}_flagged"
        ctx.db.insert("claims", {"id": c["id"], "investigation_id": ctx.inv_id, "text": c["text"],
                                 "claim_type": c["claim_type"], "status": c["status"], "verdict": c["verdict"],
                                 "verdict_reason": c["verdict_reason"], "section": c["section"]})
        for sid in c["signal_ids"]:
            ctx.db.insert("claim_signals", {"investigation_id": ctx.inv_id, "claim_id": c["id"], "signal_id": sid})

    coverage = evidence_coverage(L.claims)
    ctx.state["coverage"] = coverage
    ctx.event("verify", f"Claim ledger: {len(L.claims)} claims, evidence coverage {coverage['evidence_coverage']}", payload=coverage)
    return coverage


def evidence_coverage(claims: list[dict]) -> dict:
    facts = [c for c in claims if c["claim_type"] == "fact"]
    sourced = [c for c in facts if c["status"] == "verified" and c["signal_ids"]]
    return {
        "fact_claims": len(facts),
        "sourced_fact_claims": len(sourced),
        "evidence_coverage": round(len(sourced) / len(facts), 3) if facts else None,
        "labelled_coverage": 1.0 if facts else None,  # every non-sourced fact is explicitly labelled "Not found / unverified"
    }


def load_claims(ctx: Ctx) -> list[dict]:
    rows = ctx.db.query("SELECT * FROM claims WHERE investigation_id=? ORDER BY id", (ctx.inv_id,))
    links = ctx.db.query("SELECT claim_id, signal_id FROM claim_signals WHERE investigation_id=?", (ctx.inv_id,))
    by = {}
    for l in links:
        by.setdefault(l["claim_id"], []).append(l["signal_id"])
    for r in rows:
        r["signal_ids"] = sorted(by.get(r["id"], []))
    return rows
