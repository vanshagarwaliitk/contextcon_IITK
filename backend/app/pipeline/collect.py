"""Stage 3 (baseline) + probe executors used by the planner (stage 4).

Every executor: runs Crustdata calls, writes a `probes` row + event, updates the ledger,
returns a small JSON-able summary (counts only — the planner never sees raw rows).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any, Callable

from ..db import now_iso
from ..models import SignalType, Theme, new_signal
from ..providers.crustdata import jobs, people, web
from ..providers.crustdata.client import BudgetExceeded, CrustdataError
from . import normalise
from .context import Ctx

MIN_ROWS_THRESHOLD = 2


# ------------------------------------------------------------------ probe bookkeeping
def record_probe(
    ctx: Ctx, tool: str, params: dict, status: str, reason: str = "", rows: int = 0,
    result: Any = None, signal_ids: list[str] | None = None, credits: float = 0.0,
) -> str:
    pid = ctx.db.next_id(ctx.inv_id, "prb")
    ctx.db.insert(
        "probes",
        {
            "id": pid,
            "investigation_id": ctx.inv_id,
            "tool": tool,
            "params_json": json.dumps(params, sort_keys=True),
            "status": status,
            "reason": reason,
            "rows": rows,
            "credits_est": credits,
            "result_json": json.dumps(result, default=str)[:20000] if result is not None else None,
            "signal_ids_json": json.dumps(signal_ids or []),
            "created_at": now_iso(),
        },
    )
    ctx.event("probe", f"{tool}({_fmt(params)}) -> {status}" + (f" — {reason}" if reason else ""),
              "info" if status == "ok" else "warn", {"probe_id": pid, "result": result})
    return pid


def _fmt(params: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def _theme_qb(ctx: Ctx, theme: Theme, window_name: str | None = "recent", company_id: int | None = None) -> jobs.JobQueryBuilder:
    qb = jobs.JobQueryBuilder().company(company_id or ctx.company_id)
    if window_name:
        qb.window(ctx.windows[window_name])
    return qb.theme_match(theme.title_keywords, theme.description_phrases)


def _credits_before(ctx: Ctx) -> float:
    return ctx.crust.budget.credits


# ------------------------------------------------------------------ baseline (A5.1)
def baseline(ctx: Ctx) -> None:
    """limit:0 aggregations per window; stored as derived_metric signals with the exact request in raw_ref."""
    cid = ctx.company_id
    plan: list[tuple[str, str | None]] = []
    for w in ("recent", "prior", "older"):
        plan += [(w, None), (w, "job_details.title"), (w, "location.country")]
    plan += [("recent", "job_details.category"), ("recent", "job_details.workplace_type"), ("recent", "job_details.source")]

    base = ctx.state["baseline"]
    for wname, field in plan:
        key = f"{wname}:{field or 'count'}"
        if key in base:
            continue
        w = ctx.windows[wname]
        qb = jobs.JobQueryBuilder().company(cid).window(w)
        try:
            if field is None:
                value, body = jobs.count(ctx.crust, ctx.inv_id, qb, f"baseline_{key}")
                summary = f"{value} total postings added {w.start} to {w.end} ({wname} window)."
            else:
                value, body = jobs.group_by(ctx.crust, ctx.inv_id, qb, field, f"baseline_{key}",
                                            size=50 if field == "job_details.title" else 30)
                top = ", ".join(f"{b['key']} ({b['count']})" for b in value[:5]) or "none"
                summary = f"Top {field} values for postings added {w.start} to {w.end} ({wname}): {top}."
        except (CrustdataError, BudgetExceeded) as exc:
            ctx.warn("baseline", f"{key} failed: {exc}")
            base[key] = {"error": str(exc)[:200]}
            continue
        sig = new_signal(
            ctx.db, ctx.inv_id,
            signal_type=SignalType.derived_metric,
            title=f"Baseline {field or 'total postings'} — {wname} window",
            summary=summary,
            observed_at=w.end.isoformat(),
            source_name="crustdata_job_search",
            endpoint="/job/search",
            raw_ref=json.dumps(body, sort_keys=True),
            provider_mode=ctx.provider_mode,
            derived=True,
            computation=f"Crustdata /job/search limit:0 aggregation ({'count' if field is None else 'group_by ' + field}) "
                        f"for company {cid}, metadata.date_added in [{w.start}, {w.end}).",
            theme_tags=["baseline"],
            features={"metric": f"baseline_{field or 'count'}", "window": wname, "value": value},
        )
        base[key] = {"value": value, "signal_id": sig.id}
    ctx.event("baseline", "Baseline aggregations collected",
              payload={k: (v.get("value") if not isinstance(v.get("value"), list) else len(v["value"])) for k, v in base.items()})
    ctx.state["emerging_terms"] = emerging_terms(ctx)
    ctx.event("baseline", f"Emerging title terms: {ctx.state['emerging_terms']}")


def baseline_value(ctx: Ctx, window: str, field: str | None = None) -> Any:
    return (ctx.state["baseline"].get(f"{window}:{field or 'count'}") or {}).get("value")


# ------------------------------------------------------------------ emerging terms (A5.7)
_STOP = set(
    "a an and the of for in on at to with senior sr jr junior lead staff principal manager engineer engineering "
    "ii iii iv i intern associate specialist analyst director head vp - & / , remote hybrid".split()
)
_tok = re.compile(r"[a-z][a-z0-9+#.]*")


def _terms(title: str) -> list[str]:
    toks = [t for t in _tok.findall((title or "").lower()) if t not in _STOP and len(t) > 1]
    return toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def emerging_terms(ctx: Ctx, top_n: int = 10) -> list[dict]:
    recent = baseline_value(ctx, "recent", "job_details.title") or []
    prior = baseline_value(ctx, "prior", "job_details.title") or []
    if not recent:
        return []
    rc, pc = Counter(), Counter()
    for b in recent:
        for t in set(_terms(b["key"])):
            rc[t] += b["count"]
    for b in prior:
        for t in set(_terms(b["key"])):
            pc[t] += b["count"]
    rd, pd = ctx.windows["recent"].days, ctx.windows["prior"].days
    scored = []
    for term, n in rc.items():
        if n < 2:
            continue
        ratio = (n / rd + 0.01) / (pc.get(term, 0) / pd + 0.01)
        if ratio >= 1.5:
            scored.append({"term": term, "recent": n, "prior": pc.get(term, 0), "ratio": round(ratio, 2)})
    scored.sort(key=lambda x: (-x["ratio"] * math.log1p(x["recent"]), x["term"]))
    return scored[:top_n]


# ------------------------------------------------------------------ executors (A5.2-6, A6)
class ProbeRejected(Exception):
    pass


def _theme_or_reject(ctx: Ctx, theme_id: str) -> Theme:
    t = ctx.theme(theme_id)
    if t is None:
        raise ProbeRejected(f"unknown theme_id {theme_id}")
    return t


def job_theme_count(ctx: Ctx, theme_id: str, window: str = "recent", reason: str = "") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    if window not in ctx.windows:
        raise ProbeRejected(f"unknown window {window}")
    c0 = _credits_before(ctx)
    n, body = jobs.count(ctx.crust, ctx.inv_id, _theme_qb(ctx, theme, window), f"theme_count_{theme_id}_{window}")
    led = ctx.ledger_for(theme_id)
    led["count"][window] = n
    led.setdefault("count_bodies", {})[window] = body
    record_probe(ctx, "job_theme_count", {"theme_id": theme_id, "window": window}, "ok", reason,
                 result={"count": n}, credits=ctx.crust.budget.credits - c0)
    return {"theme_id": theme_id, "window": window, "count": n}


def job_theme_rows(ctx: Ctx, theme_id: str, reason: str = "", limit: int = 25) -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    led = ctx.ledger_for(theme_id)
    recent = led["count"].get("recent")
    if recent is None:
        raise ProbeRejected("run job_theme_count(recent) before job_theme_rows")
    if recent < MIN_ROWS_THRESHOLD:
        raise ProbeRejected(f"recent count {recent} < {MIN_ROWS_THRESHOLD}; rows not worth the credits")
    c0 = _credits_before(ctx)
    rows, body = jobs.rows(ctx.crust, ctx.inv_id, _theme_qb(ctx, theme, "recent"), f"theme_rows_{theme_id}", limit=limit)
    kept = jobs.apply_anti_keywords(rows, theme.anti_keywords)
    pid = record_probe(ctx, "job_theme_rows", {"theme_id": theme_id}, "ok", reason, rows=len(rows),
                       result={"fetched": len(rows), "after_anti_keywords": len(kept)},
                       credits=ctx.crust.budget.credits - c0)
    sigs = normalise.job_rows_to_signals(ctx, kept, theme, pid)
    led["rows_fetched"] = True
    led["row_signal_ids"] = sorted({*led["row_signal_ids"], *[s.id for s in sigs]})
    led["anti_keyword_removed"] = len(rows) - len(kept)
    ctx.db.execute("UPDATE probes SET signal_ids_json=? WHERE investigation_id=? AND id=?",
                   (json.dumps([s.id for s in sigs]), ctx.inv_id, pid))
    return {"theme_id": theme_id, "rows": len(kept), "removed_by_anti_keywords": len(rows) - len(kept)}


def job_geo(ctx: Ctx, theme_id: str, reason: str = "") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    buckets, _ = jobs.group_by(ctx.crust, ctx.inv_id, _theme_qb(ctx, theme, "recent"), "location.country",
                               f"theme_geo_{theme_id}", size=30)
    ctx.ledger_for(theme_id)["geo"] = buckets
    record_probe(ctx, "job_geo", {"theme_id": theme_id}, "ok", reason, result={"countries": buckets[:10]})
    return {"theme_id": theme_id, "countries": buckets[:10]}


def job_tech(ctx: Ctx, theme_id: str, reason: str = "") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    if not theme.tech_keywords:
        raise ProbeRejected("theme has no tech_keywords")
    qb = jobs.JobQueryBuilder().company(ctx.company_id).window(ctx.windows["recent"]).technologies(theme.tech_keywords)
    n, body = jobs.count(ctx.crust, ctx.inv_id, qb, f"theme_tech_{theme_id}")
    ctx.ledger_for(theme_id)["tech"] = {"count": n, "technologies": theme.tech_keywords}
    record_probe(ctx, "job_tech", {"theme_id": theme_id}, "ok", reason,
                 result={"count": n, "note": "technographics match professional_network postings only"})
    return {"theme_id": theme_id, "tech_postings_recent": n}


def peer_theme_share(ctx: Ctx, theme_id: str, reason: str = "") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    peers = [c for c in ctx.competitors if c.get("company_id")]
    if not peers:
        raise ProbeRejected("no resolved competitors")
    out = []
    for p in peers[:4]:
        try:
            tn, _ = jobs.count(ctx.crust, ctx.inv_id, _theme_qb(ctx, theme, "recent", p["company_id"]),
                               f"peer_theme_{theme_id}_{p['company_id']}")
            total, _ = jobs.count(ctx.crust, ctx.inv_id,
                                  jobs.JobQueryBuilder().company(p["company_id"]).window(ctx.windows["recent"]),
                                  f"peer_total_{p['company_id']}")
        except (CrustdataError,) as exc:
            ctx.warn("probe", f"peer {p['name']} failed: {exc}")
            continue
        out.append({"company": p["name"], "company_id": p["company_id"], "theme": tn, "total": total,
                    "share": round(tn / total, 4) if total else None})
    ctx.ledger_for(theme_id)["peers"] = out
    record_probe(ctx, "peer_theme_share", {"theme_id": theme_id}, "ok", reason, result=out)
    return {"theme_id": theme_id, "peers": out}


def people_leadership(ctx: Ctx, theme_id: str, reason: str = "") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    if not ctx.settings.enable_people:
        raise ProbeRejected("people probe disabled (ENABLE_PEOPLE=0)")
    try:
        found, _ = people.search_leaders(ctx.crust, ctx.inv_id, ctx.company_id, theme.title_keywords)
    except CrustdataError as exc:  # fail soft
        ctx.ledger_for(theme_id)["people"] = []
        record_probe(ctx, "people_leadership", {"theme_id": theme_id}, "failed", f"{reason} | {exc}"[:300])
        ctx.warn("probe", f"people search failed (soft): {exc}")
        return {"theme_id": theme_id, "people": 0, "error": "people search unavailable"}
    pid = record_probe(ctx, "people_leadership", {"theme_id": theme_id}, "ok", reason, rows=len(found),
                       result={"people": len(found)})
    sigs = normalise.people_to_signals(ctx, found, theme, pid)
    ctx.ledger_for(theme_id)["people"] = [s.id for s in sigs]
    return {"theme_id": theme_id, "people": len(sigs)}


def web_evidence(ctx: Ctx, theme_id: str, reason: str = "", query: str | None = None, purpose: str = "support") -> dict:
    theme = _theme_or_reject(ctx, theme_id)
    if not ctx.settings.enable_web:
        raise ProbeRejected("web probe disabled (ENABLE_WEB=0)")
    q = query or f"{ctx.company_name} {theme.label} launch OR partnership OR announces"
    try:
        results, _ = web.search(ctx.crust, ctx.inv_id, q)
        results = results[:5]
        primary = [r for r in results if web.classify_source(r["url"], ctx.company_domain) == "primary"]
        if primary:
            for page in web.fetch(ctx.crust, ctx.inv_id, [primary[0]["url"]]):
                for r in results:
                    if r["url"] == page["url"] and page.get("text"):
                        r["snippet"] = (r.get("snippet") or "") + " … " + page["text"][:300]
    except CrustdataError as exc:  # fail soft
        record_probe(ctx, "web_evidence", {"theme_id": theme_id, "query": q}, "failed", f"{reason} | {exc}"[:300])
        ctx.warn("probe", f"web evidence failed (soft): {exc}")
        if purpose == "support":
            ctx.ledger_for(theme_id)["web"] = []
        return {"theme_id": theme_id, "results": 0, "error": "web search unavailable"}
    pid = record_probe(ctx, "web_evidence", {"theme_id": theme_id, "query": q}, "ok", reason, rows=len(results),
                       result=[{"title": r["title"], "url": r["url"]} for r in results])
    sigs = normalise.web_to_signals(ctx, results, theme, pid, purpose=purpose)
    if purpose == "support":
        ctx.ledger_for(theme_id)["web"] = [s.id for s in sigs]
    return {"theme_id": theme_id, "results": len(sigs),
            "primary_sources": sum(1 for s in sigs if s.features.get("source_class") == "primary"),
            "signal_ids": [s.id for s in sigs]}


EXECUTORS: dict[str, Callable[..., dict]] = {
    "job_theme_count": job_theme_count,
    "job_theme_rows": job_theme_rows,
    "job_geo": job_geo,
    "job_tech": job_tech,
    "peer_theme_share": peer_theme_share,
    "people_leadership": people_leadership,
    "web_evidence": web_evidence,
}
