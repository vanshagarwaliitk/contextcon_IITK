"""Stage 5b — computed features as derived_metric signals. ALL numbers are computed here, never by an LLM."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from ..models import Signal, SignalType, new_signal, save_signal
from . import factors
from .collect import baseline_value
from .context import Ctx


def r3(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


# ------------------------------------------------------------------ pure math (unit-tested)
def velocity_ratio(recent: int, recent_days: int, prior: int, prior_days: int) -> float | None:
    """recent per-day rate / prior per-day rate. None when prior == 0 ("new vs prior window")."""
    if prior == 0:
        return None
    return r3((recent / recent_days) / (prior / prior_days))


def concentration(theme_count: int | None, total: int | None) -> float | None:
    if not total or theme_count is None:
        return None
    return r3(theme_count / total)


def seniority_share(tiers: list[int], min_tier: int = 3) -> float | None:
    if not tiers:
        return None
    return r3(sum(1 for t in tiers if t >= min_tier) / len(tiers))


def evergreen_ratio(rows: list[dict]) -> float | None:
    """Share of postings that are reposted, OR whose title appears in >=3 locations, OR share a description hash."""
    if not rows:
        return None
    title_locs: dict[str, set] = defaultdict(set)
    hashes = Counter(r.get("desc_hash") for r in rows if r.get("desc_hash"))
    for r in rows:
        title_locs[r.get("norm_title") or ""].add(r.get("city") or r.get("country") or "")
    flagged = 0
    for r in rows:
        if r.get("reposted") or len(title_locs[r.get("norm_title") or ""]) >= 3 or \
                (r.get("desc_hash") and hashes[r["desc_hash"]] > 1):
            flagged += 1
    return r3(flagged / len(rows))


def new_countries(recent_theme_countries: list[str], earlier_countries: list[str]) -> list[str]:
    earlier = {c for c in earlier_countries if c}
    return sorted({c for c in recent_theme_countries if c and c not in earlier})


# ------------------------------------------------------------------ test_factors.md factor features
def _factor_features(ctx: Ctx, theme, led: dict, totals: dict, base_ids: dict, f: dict, ids: dict, add) -> None:
    """Features from the factor-constrained Crustdata counts (collect.company_factor_counts / theme_factor_counts)."""
    comp = ctx.state.get("factors") or {}
    tf = led.get("factors") or {}

    def cval(w: str, fid: str):
        return (comp.get(f"{w}:{fid}") or {}).get("value")

    def cid(w: str, fid: str):
        return (comp.get(f"{w}:{fid}") or {}).get("signal_id")

    def cite(name: str, value: Any, sid: str | None) -> None:
        """Expose an existing factor-count signal as a feature (no new signal)."""
        if value is not None and sid:
            f[name], ids[name] = value, sid

    # departmental ratio, company-wide (test_factors §1 R&D vs GTM)
    for w in factors.COMPANY_WINDOWS:
        g = factors.ratio(cval(w, "gtm"), cval(w, "rnd"))
        if g is not None:
            add(f"company_gtm_share_{w}", g,
                f"factor_gtm_{w} / (factor_gtm_{w} + factor_rnd_{w}) (title-keyword counts, company-wide).",
                [cid(w, "gtm"), cid(w, "rnd")],
                f"Company-wide share of GTM vs R&D titled postings in the {w} window: {g}.")
    # seniority ratio, company-wide (§1 zero-to-one vs scale)
    for fid in ("zero_to_one", "junior", "reposted"):
        s = factors.share(cval("recent", fid), totals.get("recent"))
        if s is not None:
            add(f"company_{fid}_share_recent", s,
                f"factor_{fid}_recent / total recent postings ({totals.get('recent')}).",
                [cid("recent", fid), base_ids.get("recent")],
                f"Share of all recent company postings matching factor '{fid}': {s}.")
    # compliance keywords, company-wide (§1)
    for w in factors.COMPANY_WINDOWS:
        cite(f"company_compliance_postings_{w}", cval(w, "compliance"), cid(w, "compliance"))
    # theme-scoped counts
    for fid in ("compliance", "zero_to_one", "gtm"):
        e = tf.get(f"recent:{fid}") or {}
        cite(f"theme_{fid}_postings_recent", e.get("value"), e.get("signal_id"))
    tg = (tf.get("recent:gtm") or {})
    s = factors.share(tg.get("value"), (led.get("count") or {}).get("recent"))
    if s is not None:
        add("theme_gtm_share_recent", s, "theme_gtm_postings_recent / theme_postings_recent.",
            [tg.get("signal_id"), ids.get("theme_postings_recent")],
            f"Share of recent '{theme.label}' postings with GTM titles: {s}.")


# ------------------------------------------------------------------ stage
def _metric(ctx: Ctx, theme_id: str, name: str, value: Any, computation: str, derived_from: list[str],
            title: str, summary: str) -> Signal:
    sig = new_signal(
        ctx.db, ctx.inv_id,
        signal_type=SignalType.derived_metric,
        title=title,
        summary=summary,
        observed_at=ctx.windows["recent"].end.isoformat(),
        source_name="coredata_computed",
        endpoint="/job/search",
        provider_mode=ctx.provider_mode,
        derived=True,
        derived_from=derived_from,
        computation=computation,
        theme_tags=[theme_id],
        features={"metric": name, "value": value, "theme_id": theme_id},
    )
    ctx.db.insert("signal_features", {
        "investigation_id": ctx.inv_id, "theme_id": theme_id, "name": name,
        "value_json": json.dumps(value), "signal_id": sig.id,
    })
    return sig


def run(ctx: Ctx) -> dict[str, dict]:
    ctx.db.execute("DELETE FROM signal_features WHERE investigation_id=?", (ctx.inv_id,))
    signals = ctx.signals()
    W = ctx.windows
    totals = {w: baseline_value(ctx, w) for w in W}
    base_ids = {w: (ctx.state["baseline"].get(f"{w}:count") or {}).get("signal_id") for w in W}
    earlier_countries = [b["key"] for w in ("prior", "older") for b in (baseline_value(ctx, w, "location.country") or [])]
    all_features: dict[str, dict] = {}

    for theme in ctx.themes():
        led = ctx.ledger_for(theme.id)
        counts = led["count"]
        rows = [signals[i] for i in led["row_signal_ids"] if i in signals]
        row_ids = [s.id for s in rows]
        f: dict[str, Any] = {}
        ids: dict[str, str] = {}

        def add(name: str, value: Any, computation: str, derived_from: list[str], summary: str) -> None:
            s = _metric(ctx, theme.id, name, value, computation, [d for d in derived_from if d],
                        f"{theme.label}: {name.replace('_', ' ')}", summary)
            f[name], ids[name] = value, s.id

        for w in ("recent", "prior", "older"):
            if counts.get(w) is not None:
                add(f"theme_postings_{w}", counts[w],
                    f"/job/search limit:0 count with theme filter, {w} window [{W[w].start}, {W[w].end}).",
                    [], f"{counts[w]} postings matched theme '{theme.label}' in the {w} window ({W[w].start} to {W[w].end}).")

        if counts.get("recent") is not None and counts.get("prior") is not None:
            v = velocity_ratio(counts["recent"], W["recent"].days, counts["prior"], W["prior"].days)
            label = v if v is not None else "new vs prior window"
            add("velocity_ratio", label,
                f"(theme_postings_recent / {W['recent'].days}) / (theme_postings_prior / {W['prior'].days}); None if prior == 0.",
                [ids.get("theme_postings_recent"), ids.get("theme_postings_prior")],
                f"Theme '{theme.label}' posting rate, recent vs prior window: {label}.")

        for w in ("recent", "older"):
            c = concentration(counts.get(w), totals.get(w))
            if c is not None:
                add(f"concentration_{w}", c, f"theme_postings_{w} / total postings in {w} window ({totals.get(w)}).",
                    [ids.get(f"theme_postings_{w}"), base_ids.get(w)],
                    f"Theme '{theme.label}' share of all {w}-window postings: {c}.")

        tiers = [s.features.get("seniority_tier", 1) for s in rows]
        ss = seniority_share(tiers)
        if ss is not None:
            add("seniority_share", ss, "share of fetched theme postings with seniority tier >= lead (regex tiers).",
                row_ids, f"Share of fetched '{theme.label}' postings at lead level or above: {ss}.")

        rc = [b["key"] for b in (led.get("geo") or [])] or [s.features.get("country") for s in rows]
        if rc:
            nc = new_countries(rc, earlier_countries)
            add("geo_new_countries", nc,
                "countries in recent theme postings absent from ALL company postings in prior+older windows.",
                row_ids + [base_ids.get("prior"), base_ids.get("older")],
                f"Countries with recent '{theme.label}' postings not seen in earlier company postings: {', '.join(nc) or 'none'}.")

        er = evergreen_ratio([s.features for s in rows])
        if er is not None:
            add("evergreen_ratio", er, "share of fetched postings reposted OR same title in >=3 locations OR duplicate description hash.",
                row_ids, f"Share of fetched '{theme.label}' postings that look evergreen/duplicated: {er}.")

        srcs = sorted({s.source_name for s in rows if s.source_name})
        if rows:
            add("source_platforms", len(srcs), "count of distinct job_details.source among fetched postings.",
                row_ids, f"Fetched '{theme.label}' postings come from {len(srcs)} job platform(s): {', '.join(srcs)}.")

        peers = [p for p in (led.get("peers") or []) if p.get("share") is not None]
        if peers and f.get("concentration_recent") is not None:
            mean_peer = sum(p["share"] for p in peers) / len(peers)
            gap = r3(f["concentration_recent"] - mean_peer)
            add("peer_share_gap", gap,
                f"concentration_recent - mean theme share across {len(peers)} competitors ({r3(mean_peer)}).",
                [ids.get("concentration_recent")],
                f"Theme '{theme.label}' share vs competitor mean ({', '.join(p['company'] for p in peers)}): gap {gap}.")
            f["peer_mean_share"] = r3(mean_peer)

        if led.get("tech") is not None:
            add("tech_postings_recent", led["tech"]["count"], "recent postings with technographics.technologies.name in tech_keywords.",
                [], f"{led['tech']['count']} recent postings mention {', '.join(led['tech']['technologies'])}.")

        _factor_features(ctx, theme, led, totals, base_ids, f, ids, add)

        # singletons -> weak
        if len(rows) == 1:
            rows[0].status = "weak"
            save_signal(ctx.db, rows[0])

        f["_ids"] = ids
        all_features[theme.id] = f
    ctx.state["features"] = all_features
    ctx.event("features", "Computed theme features", payload={k: {n: v for n, v in f.items() if n != "_ids"} for k, f in all_features.items()})
    return all_features
