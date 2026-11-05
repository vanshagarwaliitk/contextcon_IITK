"""Stage 9 — rule-based evidence strength (Python only). Output: Low | Medium | High + breakdown. Never percentages."""
from __future__ import annotations

import json
import statistics
from datetime import date

from ..models import Signal, SignalType
from . import scoring_config as C
from .context import Ctx
from .falsify import load_tests
from .hypothesize import load_hypotheses


def _age_days(observed_at: str | None, today: date) -> int | None:
    if not observed_at:
        return None
    try:
        return (today - date.fromisoformat(str(observed_at)[:10])).days
    except ValueError:
        return None


def label_for(points: int) -> str:
    if points >= C.HIGH_MIN:
        return "High"
    if points >= C.MEDIUM_MIN:
        return "Medium"
    return "Low"


def score(h: dict, signals: dict[str, Signal], tests: list[dict], features: dict, today: date) -> dict:
    support = [signals[i] for i in h["supporting_signal_ids"] if i in signals and not signals[i].derived]
    rules: list[dict] = []

    def rule(name: str, pts: int, reason: str) -> None:
        rules.append({"rule": name, "points": pts, "reason": reason})

    n = len({s.id for s in support})
    rule("distinct_supporting_signals",
         C.DISTINCT_SIGNALS_3_PLUS if n >= 3 else C.DISTINCT_SIGNALS_2 if n == 2 else 0, f"{n} distinct supporting signals")

    types = {s.signal_type for s in support}
    rule("signal_type_diversity", C.TYPE_DIVERSITY_POINTS if len(types) >= C.TYPE_DIVERSITY_MIN else 0,
         f"{len(types)} signal type(s): {', '.join(sorted(t.value for t in types)) or 'none'}")

    primary = [s for s in support if s.signal_type == SignalType.web_evidence and s.features.get("source_class") == "primary"]
    sources = {s.source_name for s in support if s.signal_type == SignalType.hiring_cluster and s.source_name}
    if primary:
        sources.add("web_primary")
    rule("independent_sources", C.SOURCE_PLATFORMS_POINTS if len(sources) >= C.SOURCE_PLATFORMS_MIN else 0,
         f"{len(sources)} independent source(s)")

    ages = [a for a in (_age_days(s.observed_at, today) for s in support) if a is not None]
    med = statistics.median(ages) if ages else None
    rule("recency", C.RECENCY_POINTS if med is not None and med <= C.RECENCY_MAX_MEDIAN_DAYS else 0,
         f"median supporting signal age {med if med is not None else 'unknown'} days")

    v, recent = features.get("velocity_ratio"), features.get("theme_postings_recent") or 0
    vel_ok = recent >= C.VELOCITY_MIN_RECENT and (v == "new vs prior window" or (isinstance(v, (int, float)) and v >= C.VELOCITY_MIN_RATIO))
    rule("velocity", C.VELOCITY_POINTS if vel_ok else 0, f"velocity {v}, recent postings {recent}")

    senior = any((s.features.get("seniority_tier") or 0) >= C.SENIORITY_MIN_TIER for s in support)
    rule("seniority", C.SENIORITY_POINTS if senior else 0, "lead-or-above role present" if senior else "no lead-or-above roles")

    rule("primary_corroboration", C.PRIMARY_CORROBORATION_POINTS if primary else 0,
         f"{len(primary)} primary-source web signal(s)")

    contradicted = [t for t in tests if t["outcome"] == "contradicts"]
    for t in contradicted:
        rule(f"contradiction:{t['test_id']}", C.CONTRADICTION_PENALTY.get(t["test_id"], -1), t["explanation"] or "")

    total = sum(r["points"] for r in rules)
    label = label_for(total)
    caps: list[str] = []

    def cap(reason: str) -> None:
        nonlocal label
        if label == "High":
            label = "Medium"
            caps.append(reason)

    if len(types) <= 1:
        cap("only one signal type")
    if not any((a := _age_days(s.observed_at, today)) is not None and a <= C.RECENT_WINDOW_DAYS for s in support):
        cap("no recent-window evidence")
    sup_ids = {s.id for s in support}
    for ref in ("H2", "H3"):
        consistent = {m["signal_id"] for m in h.get("matrix", []) if m["hypothesis_ref"] == ref and m["relation"] == "consistent"}
        if sup_ids and sup_ids <= consistent:
            cap(f"alternative {ref} is consistent with ALL supporting signals")
            break

    # evidence status (PRD 6.2 / PLAYBOOK 0.5)
    if h.get("contradicting_signal_ids") or contradicted:
        status = "conflicting"
    elif len(sources) >= 2:
        status = "corroborated"
    elif support:
        status = "single_source"
    else:
        status = "unverified"
    return {"label": label, "points": total, "rules": rules, "caps_applied": caps, "evidence_status": status}


def run(ctx: Ctx) -> None:
    signals = ctx.signals()
    feats = ctx.state.get("features", {})
    for h in load_hypotheses(ctx):
        if h["hypothesis_type"] == "insufficient_evidence":
            res = {"label": "Low", "points": 0, "rules": [], "caps_applied": ["insufficient_evidence"], "evidence_status": "unverified"}
        else:
            res = score(h, signals, load_tests(ctx, h["id"]), feats.get(h["theme_id"], {}), ctx.today)
        ctx.db.execute(
            "UPDATE hypotheses SET evidence_strength=?, evidence_status=?, score_breakdown_json=? WHERE investigation_id=? AND id=?",
            (res["label"], res["evidence_status"], json.dumps(res), ctx.inv_id, h["id"]),
        )
        ctx.event("score", f"{h['id']}: {res['label']} ({res['evidence_status']})", payload=res)
