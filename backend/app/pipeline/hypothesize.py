"""Stage 7 — hypothesis engine (ACH-lite, LLM B4) with strict Python validators + repair + fallback."""
from __future__ import annotations

import json

from ..llm.client import LLMError, LLMValidationError
from ..llm.schemas import HypothesisOut, HypothesisResult
from ..models import HYPOTHESIS_TYPES, Signal, SignalType
from . import guards
from .cluster import load_clusters
from .context import Ctx

FEATURE_KEYS = ["theme_postings_recent", "theme_postings_prior", "theme_postings_older", "velocity_ratio",
                "concentration_recent", "concentration_older", "seniority_share", "geo_new_countries",
                "evergreen_ratio", "peer_share_gap", "source_platforms", "tech_postings_recent"]
CONTRACTION_WORDS = ("contraction", "cost", "slowdown", "slowing", "reduc", "declin", "falling", "fewer")


def is_falling(features: dict) -> bool:
    v = features.get("velocity_ratio")
    return isinstance(v, (int, float)) and v < 1.0


def build_inputs(ctx: Ctx, cluster: dict, signals: dict[str, Signal]) -> dict:
    feats = ctx.state.get("features", {}).get(cluster["theme_id"], {})
    ids = feats.get("_ids", {})
    led = ctx.ledger_for(cluster["theme_id"])
    sigs = [signals[i] for i in cluster["signal_ids"] if i in signals]
    return {
        "user_question": ctx.user_question or "(none)",
        "theme": ctx.theme(cluster["theme_id"]).model_dump(),
        "cluster_signals": "\n".join(s.compact() for s in sigs),
        "features_with_ids": [{"signal_id": ids[k], "feature": k, "value": feats[k]} for k in FEATURE_KEYS if k in feats],
        "peer_features": led.get("peers") or "no competitor data",
    }


def validate(h_list: HypothesisResult, allowed_ids: set[str], signals: dict[str, Signal],
             allowed_nums: set[str], falling: bool) -> list[str]:
    errs: list[str] = []
    if not h_list.hypotheses:
        return ["return at least one hypothesis (use insufficient_evidence if thin)"]
    if len(h_list.hypotheses) > 2:
        errs.append("at most 2 hypotheses per cluster")
    for n, h in enumerate(h_list.hypotheses, 1):
        p = f"hypothesis {n}"
        cited = set(h.supporting_signal_ids) | set(h.contradicting_signal_ids)
        for a in h.alternatives:
            cited |= set(a.consistent_signal_ids)
        unknown = sorted(cited - allowed_ids) + sorted({m.signal_id for m in h.matrix} - allowed_ids)
        if unknown:
            errs.append(f"{p}: unknown/foreign signal ids {sorted(set(unknown))}")
        if h.hypothesis_type not in HYPOTHESIS_TYPES:
            errs.append(f"{p}: invalid hypothesis_type")
        if h.hypothesis_type != "insufficient_evidence" and len(set(h.supporting_signal_ids)) < 2:
            errs.append(f"{p}: needs >= 2 distinct supporting signals (or use insufficient_evidence)")
        if not guards.starts_safe(h.statement):
            errs.append(f"{p}: statement must start with 'Signals suggest', 'May indicate' or 'Worth investigating'")
        has_primary = any(signals[i].signal_type == SignalType.web_evidence and signals[i].features.get("source_class") == "primary"
                          for i in h.supporting_signal_ids if i in signals)
        for field, text in [("title", h.title), ("statement", h.statement),
                            *[(f"alternative {a.ref}", a.statement) for a in h.alternatives]]:
            banned = guards.banned_in(text)
            if banned and not has_primary:
                errs.append(f"{p}: banned certainty phrase(s) {banned} in {field}")
            bad = guards.ungrounded(text, allowed_nums)
            if bad:
                errs.append(f"{p}: ungrounded number(s) {bad} in {field}; use only numbers present in the input or none")
        refs = {a.ref for a in h.alternatives}
        if not {"H2", "H3"} <= refs:
            errs.append(f"{p}: alternatives must include H2 (benign) and H3 (noise)")
        need = {(sid, r) for sid in cited & allowed_ids for r in ("H1", "H2", "H3")}
        have = {(m.signal_id, m.hypothesis_ref) for m in h.matrix}
        missing = need - have
        if missing:
            errs.append(f"{p}: matrix missing {len(missing)} cells, e.g. {sorted(missing)[:4]}")
    if falling and not any(
        h.hypothesis_type == "contraction_or_cost_cutting"
        or any(w in a.statement.lower() for a in h.alternatives for w in CONTRACTION_WORDS)
        for h in h_list.hypotheses
    ):
        errs.append("postings are falling (velocity_ratio < 1): consider contraction_or_cost_cutting as a hypothesis or alternative")
    return errs


def fallback_hypothesis(cluster: dict, reason: str) -> HypothesisOut:
    return HypothesisOut(
        hypothesis_type="insufficient_evidence",
        title=f"Insufficient evidence for a strategic hypothesis: {cluster['label']}",
        statement="Worth investigating further: the collected signals could not be turned into a validated hypothesis.",
        supporting_signal_ids=[], contradicting_signal_ids=[], alternatives=[],
        evidence_gaps=[f"Automated hypothesis generation failed validation ({reason[:120]})."],
        recommended_next_indicators=["Re-run with more probes or a narrower theme definition"],
        matrix=[], diagnostic_note="Templated fallback; no interpretation was made.",
    )


def save(ctx: Ctx, cluster: dict, h: HypothesisOut) -> str:
    hid = ctx.db.next_id(ctx.inv_id, "hyp")
    ctx.db.insert("hypotheses", {
        "id": hid, "investigation_id": ctx.inv_id, "cluster_id": cluster["id"], "theme_id": cluster["theme_id"],
        "hypothesis_type": h.hypothesis_type, "title": h.title, "explanation": h.statement,
        "status": "hypothesis",
        "alternatives_json": json.dumps([a.model_dump() for a in h.alternatives]),
        "evidence_gaps_json": json.dumps(h.evidence_gaps),
        "next_indicators_json": json.dumps(h.recommended_next_indicators),
        "diagnostic_note": h.diagnostic_note,
    })
    for sid in dict.fromkeys(h.supporting_signal_ids):
        ctx.db.insert("hypothesis_signals", {"investigation_id": ctx.inv_id, "hypothesis_id": hid, "signal_id": sid, "relationship": "supports"})
    for sid in dict.fromkeys(h.contradicting_signal_ids):
        ctx.db.insert("hypothesis_signals", {"investigation_id": ctx.inv_id, "hypothesis_id": hid, "signal_id": sid, "relationship": "contradicts"})
    for m in h.matrix:
        ctx.db.insert("hypothesis_matrix", {"investigation_id": ctx.inv_id, "hypothesis_id": hid,
                                            "hypothesis_ref": m.hypothesis_ref, "signal_id": m.signal_id, "relation": m.relation})
    ctx.event("hypothesize", f"{hid} [{h.hypothesis_type}] {h.title}",
              payload={"supporting": h.supporting_signal_ids, "contradicting": h.contradicting_signal_ids})
    return hid


def run(ctx: Ctx) -> list[str]:
    signals = ctx.signals()
    made: list[str] = []
    clusters = load_clusters(ctx, status="qualifying")
    if not clusters:
        ctx.event("hypothesize", "No qualifying clusters; no hypotheses generated (insufficient evidence)")
        return made
    for cl in clusters:
        inputs = build_inputs(ctx, cl, signals)
        allowed_ids = set(cl["signal_ids"]) | {f["signal_id"] for f in inputs["features_with_ids"]}
        allowed_nums = guards.allowed_numbers(inputs["cluster_signals"], inputs["features_with_ids"], inputs["peer_features"],
                                              inputs["user_question"])
        falling = is_falling(ctx.state.get("features", {}).get(cl["theme_id"], {}))
        try:
            res = ctx.llm.structured(
                "b4_hypothesis.md", inputs, HypothesisResult,
                validator=lambda r: validate(r, allowed_ids, signals, allowed_nums, falling),
                effort="high",
            )
            hyps = res.hypotheses[:2]
        except LLMValidationError as exc:
            ctx.warn("hypothesize", f"{cl['id']}: hypotheses failed validation after repairs -> templated fallback")
            hyps = [fallback_hypothesis(cl, str(exc))]
        except LLMError as exc:
            ctx.warn("hypothesize", f"LLM unavailable ({exc}); report will contain facts + features only, no hypotheses")
            ctx.state["llm_down"] = True
            return made
        for h in hyps:
            made.append(save(ctx, cl, h))
    return made


def load_hypotheses(ctx: Ctx) -> list[dict]:
    rows = ctx.db.query("SELECT * FROM hypotheses WHERE investigation_id=? ORDER BY id", (ctx.inv_id,))
    for r in rows:
        links = ctx.db.query("SELECT signal_id, relationship FROM hypothesis_signals WHERE investigation_id=? AND hypothesis_id=?",
                             (ctx.inv_id, r["id"]))
        r["supporting_signal_ids"] = sorted({l["signal_id"] for l in links if l["relationship"] == "supports"})
        r["contradicting_signal_ids"] = sorted({l["signal_id"] for l in links if l["relationship"] == "contradicts"})
        r["matrix"] = ctx.db.query("SELECT hypothesis_ref, signal_id, relation FROM hypothesis_matrix WHERE investigation_id=? AND hypothesis_id=?",
                                   (ctx.inv_id, r["id"]))
        r["alternatives"] = json.loads(r["alternatives_json"] or "[]")
        r["evidence_gaps"] = json.loads(r["evidence_gaps_json"] or "[]")
        r["recommended_next_indicators"] = json.loads(r["next_indicators_json"] or "[]")
        r["score_breakdown"] = json.loads(r["score_breakdown_json"] or "{}")
    return rows
