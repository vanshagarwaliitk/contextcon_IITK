"""Stage 8 — falsification: deterministic Python test registry; the LLM (B5) only SELECTS tests."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from ..llm.client import LLMError
from ..llm.schemas import FalsifyPlan
from ..models import Signal, SignalType
from ..providers.crustdata.client import BudgetExceeded, CrustdataError
from . import collect
from .context import Ctx
from .hypothesize import load_hypotheses

EVERGREEN_MAX = 0.4
MIN_CONCENTRATION = 0.02
MIN_POSTINGS = 3
NOVELTY_SIMILAR = 1.25   # recent share <= 1.25x older share -> "not emerging"
PEER_SIMILAR = 0.8       # peers' mean share >= 80% of company share -> industry trend
MANDATORY = ["novelty_test", "evergreen_test", "scale_test", "peer_test"]


@dataclass
class TestResult:
    test_id: str
    outcome: str  # supports | contradicts | neutral | inconclusive
    explanation: str
    evidence_signal_ids: list[str] = field(default_factory=list)


Feat = dict
TestFn = Callable[[dict, dict[str, Signal], Feat], TestResult]


def _ids(f: Feat, *names: str) -> list[str]:
    return [f["_ids"][n] for n in names if n in f.get("_ids", {})]


def novelty_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    rec, old = f.get("concentration_recent"), f.get("concentration_older")
    ids = _ids(f, "concentration_recent", "concentration_older", "theme_postings_older")
    if rec is None or old is None:
        return TestResult("novelty_test", "inconclusive", "Missing recent or older-window share.", ids)
    if old == 0 and rec > 0:
        return TestResult("novelty_test", "supports", f"Theme absent in older window (share {old}), present now ({rec}).", ids)
    if rec <= old * NOVELTY_SIMILAR:
        return TestResult("novelty_test", "contradicts", f"Theme share recent {rec} vs older {old}: not new — weakens 'emerging'.", ids)
    return TestResult("novelty_test", "supports", f"Theme share rose from {old} (older) to {rec} (recent).", ids)


def evergreen_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    er = f.get("evergreen_ratio")
    ids = _ids(f, "evergreen_ratio")
    if er is None:
        return TestResult("evergreen_test", "inconclusive", "No fetched postings to check for reposts/duplicates.", ids)
    if er > EVERGREEN_MAX:
        return TestResult("evergreen_test", "contradicts", f"Evergreen/duplicate ratio {er} > {EVERGREEN_MAX}.", ids)
    return TestResult("evergreen_test", "supports", f"Evergreen/duplicate ratio {er} <= {EVERGREEN_MAX}.", ids)


def scale_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    n, c = f.get("theme_postings_recent"), f.get("concentration_recent")
    ids = _ids(f, "theme_postings_recent", "concentration_recent")
    if n is None:
        return TestResult("scale_test", "inconclusive", "No recent theme count.", ids)
    if n < MIN_POSTINGS:
        return TestResult("scale_test", "contradicts", f"Only {n} recent theme postings (< {MIN_POSTINGS}).", ids)
    if c is not None and c < MIN_CONCENTRATION:
        return TestResult("scale_test", "contradicts", f"Theme is {c} of recent postings (< {MIN_CONCENTRATION}): small vs total hiring.", ids)
    return TestResult("scale_test", "supports", f"{n} recent theme postings, share {c}.", ids)


def peer_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    gap, mean, own = f.get("peer_share_gap"), f.get("peer_mean_share"), f.get("concentration_recent")
    ids = _ids(f, "peer_share_gap")
    if gap is None or mean is None or own is None:
        return TestResult("peer_test", "inconclusive", "No competitor baseline available.", ids)
    if own == 0 or mean >= own * PEER_SIMILAR:
        return TestResult("peer_test", "contradicts", f"Competitors' mean share {mean} vs company {own}: looks like an industry trend.", ids)
    return TestResult("peer_test", "supports", f"Company share {own} exceeds competitor mean {mean} (gap {gap}).", ids)


def seniority_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    ss = f.get("seniority_share")
    ids = _ids(f, "seniority_share")
    if ss is None:
        return TestResult("seniority_test", "inconclusive", "No fetched postings.", ids)
    if ss == 0:
        return TestResult("seniority_test", "neutral", "No lead/staff/principal/director+ roles among fetched postings.", ids)
    return TestResult("seniority_test", "supports", f"Lead-or-above share among fetched postings: {ss}.", ids)


def geo_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    nc = f.get("geo_new_countries")
    ids = _ids(f, "geo_new_countries")
    if nc is None:
        return TestResult("geo_test", "inconclusive", "No geography data for theme postings.", ids)
    if h["hypothesis_type"] != "new_geography":
        return TestResult("geo_test", "neutral", "Not a geography hypothesis.", ids)
    if nc:
        return TestResult("geo_test", "supports", f"New countries in recent theme postings: {', '.join(nc)}.", ids)
    return TestResult("geo_test", "contradicts", "No countries new relative to earlier postings.", ids)


def corroboration_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    prim = [x.id for x in s.values() if x.signal_type == SignalType.web_evidence and h["theme_id"] in x.theme_tags
            and x.features.get("source_class") == "primary" and x.features.get("purpose") != "disconfirm"]
    if prim:
        return TestResult("corroboration_test", "supports", f"{len(prim)} primary-source web page(s) found for the theme.", prim)
    return TestResult("corroboration_test", "neutral", "No primary-source (company domain) web evidence found.", [])


def source_diversity_test(h: dict, s: dict[str, Signal], f: Feat) -> TestResult:
    srcs = {s[i].source_name for i in h["supporting_signal_ids"] if i in s and s[i].source_name and not s[i].derived}
    ids = [i for i in h["supporting_signal_ids"] if i in s and not s[i].derived]
    if len(srcs) >= 2:
        return TestResult("source_diversity_test", "supports", f"{len(srcs)} distinct sources: {', '.join(sorted(srcs))}.", ids)
    return TestResult("source_diversity_test", "neutral", f"Only {len(srcs)} distinct source(s) behind supporting signals.", ids)


REGISTRY: dict[str, TestFn] = {
    "novelty_test": novelty_test, "evergreen_test": evergreen_test, "scale_test": scale_test,
    "peer_test": peer_test, "seniority_test": seniority_test, "geo_test": geo_test,
    "corroboration_test": corroboration_test, "source_diversity_test": source_diversity_test,
}


def select_tests(picked: list[str], hypothesis_type: str) -> list[str]:
    """Keep known ids only, always include the mandatory four, de-dupe preserving order."""
    out = [t for t in picked if t in REGISTRY]
    for m in MANDATORY + ["corroboration_test", "source_diversity_test"]:
        if m not in out:
            out.append(m)
    if hypothesis_type == "new_geography" and "geo_test" not in out:
        out.append("geo_test")
    return list(dict.fromkeys(out))


def run(ctx: Ctx) -> None:
    signals = ctx.signals()
    all_feats = ctx.state.get("features", {})
    for h in load_hypotheses(ctx):
        if h["hypothesis_type"] == "insufficient_evidence":
            continue
        f = all_feats.get(h["theme_id"], {})
        picked: list[str] = []
        queries: list[str] = []
        try:
            plan = ctx.llm.structured(
                "b5_falsify.md",
                {"hypothesis": {k: h[k] for k in ("id", "hypothesis_type", "title", "explanation")},
                 "coverage": {k: v for k, v in f.items() if k != "_ids"}},
                FalsifyPlan, effort="low",
            )
            picked = [t.test_id for t in plan.tests]
            dropped = [t for t in picked if t not in REGISTRY]
            if dropped:
                ctx.warn("falsify", f"{h['id']}: dropped unknown test ids {dropped}")
            queries = plan.disconfirming_web_queries[:2]
        except LLMError as exc:
            ctx.warn("falsify", f"LLM test selection unavailable ({exc}); running full registry")
        for tid in select_tests(picked, h["hypothesis_type"]):
            res = REGISTRY[tid](h, signals, f)
            _store(ctx, h["id"], res, reason="selected" if tid in picked else "mandatory")
        # disconfirming web queries (budget permitting)
        for q in queries:
            if not ctx.settings.enable_web or ctx.crust.budget.calls_left < 3:
                break
            try:
                r = collect.web_evidence(ctx, h["theme_id"], reason=f"disconfirm {h['id']}", query=q, purpose="disconfirm")
            except (collect.ProbeRejected, CrustdataError, BudgetExceeded) as exc:
                ctx.warn("falsify", f"disconfirming search failed: {exc}")
                continue
            _store(ctx, h["id"], TestResult("disconfirm_web", "inconclusive",
                                            f"Disconfirming search '{q}' returned {r.get('results', 0)} result(s); review them manually.",
                                            r.get("signal_ids", [])), reason="LLM-requested disconfirming query")
        ctx.event("falsify", f"{h['id']}: tests executed")


def _store(ctx: Ctx, hid: str, res: TestResult, reason: str) -> None:
    tid = ctx.db.next_id(ctx.inv_id, "tst")
    ctx.db.insert("tests", {
        "id": tid, "investigation_id": ctx.inv_id, "hypothesis_id": hid, "test_id": res.test_id,
        "outcome": res.outcome, "explanation": res.explanation,
        "evidence_signal_ids_json": json.dumps(res.evidence_signal_ids), "reason": reason,
    })
    rel = {"supports": "supports", "contradicts": "contradicts"}.get(res.outcome)
    if rel:  # contradictions are shown, never silently reconciled
        for sid in res.evidence_signal_ids:
            ctx.db.insert("hypothesis_signals", {"investigation_id": ctx.inv_id, "hypothesis_id": hid,
                                                 "signal_id": sid, "relationship": rel})
    ctx.event("falsify", f"{hid} {res.test_id}: {res.outcome} — {res.explanation}",
              "warn" if res.outcome == "contradicts" else "info")


def load_tests(ctx: Ctx, hid: str) -> list[dict]:
    rows = ctx.db.query("SELECT * FROM tests WHERE investigation_id=? AND hypothesis_id=? ORDER BY id", (ctx.inv_id, hid))
    for r in rows:
        r["evidence_signal_ids"] = json.loads(r["evidence_signal_ids_json"] or "[]")
    return rows
