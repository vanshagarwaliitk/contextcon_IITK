"""Stage 2 — company resolution (deterministic) + enrichment + competitor set."""
from __future__ import annotations

import json
from typing import Any

from ..models import SignalType, new_signal
from ..providers.crustdata import company as co
from ..providers.crustdata.client import BudgetExceeded, CrustdataError
from ..providers.crustdata.jobs import get_path
from .context import Ctx, NeedsConfirmation

CLEAR_MARGIN = 0.15  # top candidate must lead the second by this much (relative)


def decide(candidates: list[co.Candidate], domain: str | None) -> tuple[str, co.Candidate | None]:
    """Resolution policy. Returns (status, chosen) with status in matched|needs_confirmation|ambiguous|not_found.
    Never thresholds on absolute confidence_score (scale is inconsistent)."""
    if not candidates:
        return "not_found", None
    d = co.normalise_domain(domain)
    if d:
        for c in candidates:
            if c.primary_domain == d:
                return "matched", c
    if len(candidates) == 1 and candidates[0].primary_domain:
        return "needs_confirmation", candidates[0]
    top, second = candidates[0], candidates[1] if len(candidates) > 1 else None
    if second is not None and top.primary_domain and top.confidence_score is not None and second.confidence_score is not None:
        lead = top.confidence_score - second.confidence_score
        if top.confidence_score > 0 and lead / top.confidence_score >= CLEAR_MARGIN:
            return "needs_confirmation", top
    return "ambiguous", None


# Candidate field paths inside enrich company_data — only fields actually returned become signals.
PROFILE_FIELDS: list[tuple[str, list[str]]] = [
    ("headcount_total", ["headcount.total", "headcount.headcount", "basic_info.employee_count"]),
    ("employee_count_range", ["basic_info.employee_count_range", "headcount.range"]),
    ("industries", ["basic_info.industries", "taxonomy.industries"]),
    ("last_fundraise_date", ["funding.last_fundraise_date", "funding.last_funding_date"]),
    ("total_funding_usd", ["funding.total_investment_usd", "funding.total_funding_usd"]),
    ("last_funding_round_type", ["funding.last_funding_round_type", "funding.last_round_type"]),
    ("hq_location", ["locations.headquarters", "locations.hq_location", "basic_info.headquarters"]),
    ("year_founded", ["basic_info.year_founded", "basic_info.founded_year"]),
]


def _first(data: dict, paths: list[str]) -> Any:
    for p in paths:
        v = get_path(data, p)
        if v not in (None, "", [], {}):
            return v
    return None


def profile_signals(ctx: Ctx, profile: dict, endpoint: str = "/company/enrich") -> list[str]:
    ids = []
    name = ctx.company_name or "company"
    for key, paths in PROFILE_FIELDS:
        value = _first(profile, paths)
        if value is None:
            continue
        shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        sig = new_signal(
            ctx.db,
            ctx.inv_id,
            signal_type=SignalType.company_change,
            title=f"{name} profile: {key.replace('_', ' ')}",
            summary=f"Crustdata company profile lists {key.replace('_', ' ')} = {shown}.",
            observed_at=str(value) if key == "last_fundraise_date" else None,
            source_name="crustdata_company_profile",
            source_url=None,
            endpoint=endpoint,
            raw_ref=f"{endpoint}#{key}",
            provider_mode=ctx.provider_mode,
            features={"profile_field": key, "value": value},
        )
        ids.append(sig.id)
    return ids


def run(ctx: Ctx) -> None:
    inv = ctx.inv
    stage = "resolve"
    if inv.get("resolved_company_id") is None:
        ctx.event(stage, "Calling /company/identify", payload={"name": inv.get("company_name"), "domain": inv.get("domain")})
        cands = co.identify(ctx.crust, ctx.inv_id, name=inv.get("company_name"), domain=inv.get("domain"))
        status, chosen = decide(cands, inv.get("domain"))
        ctx.db.update_investigation(
            ctx.inv_id, candidates_json=json.dumps([c.model_dump() for c in cands[:5]], default=str)
        )
        if status in ("ambiguous", "not_found"):
            ctx.event(stage, f"Company match is {status}; waiting for user to pick/correct", "warn",
                      [c.model_dump(exclude={"basic_info"}) for c in cands[:5]])
            raise NeedsConfirmation(status)
        if status == "needs_confirmation":
            ctx.event(stage, f"Name-only match '{chosen.name}' ({chosen.primary_domain}) needs confirmation", "warn")
            raise NeedsConfirmation(status)
        set_company(ctx, chosen)
        ctx.event(stage, f"Matched {chosen.name} ({chosen.primary_domain}) id={chosen.crustdata_company_id}")

    # ---- enrich
    if ctx.state.get("profile_done") is not True:
        try:
            profile = co.enrich(ctx.crust, ctx.inv_id, ctx.company_id)
        except (CrustdataError, BudgetExceeded) as exc:
            ctx.warn(stage, f"enrich failed: {exc}")
            profile = None
        if profile:
            ctx.db.update_investigation(ctx.inv_id, profile_json=json.dumps(profile, default=str)[:200000])
            ids = profile_signals(ctx, profile)
            ctx.event(stage, f"Stored profile; {len(ids)} profile signals", payload=ids)
        else:
            basic = (ctx.db.get_investigation(ctx.inv_id) or {}).get("candidates_json")
            ctx.warn(stage, "No enrichment profile returned; profile fields shown as 'Not found'")
            if basic:
                cands = json.loads(basic)
                if cands:
                    profile_signals(ctx, {"basic_info": cands[0].get("basic_info") or {}}, endpoint="/company/identify")
        ctx.state["profile_done"] = True

    # ---- competitors (identify only)
    if "competitors" not in ctx.state:
        resolved = []
        for comp in json.loads(inv.get("competitors_json") or "[]"):
            label = comp if isinstance(comp, str) else (comp.get("domain") or comp.get("name"))
            if not label:
                continue
            is_domain = "." in label and " " not in label
            try:
                cands = co.identify(ctx.crust, ctx.inv_id, name=None if is_domain else label,
                                    domain=label if is_domain else None)
            except (CrustdataError, BudgetExceeded) as exc:
                ctx.warn(stage, f"competitor '{label}' identify failed: {exc}")
                continue
            status, chosen = decide(cands, label if is_domain else None)
            if chosen is None and cands:
                chosen = cands[0]
                status = "best_guess"
            if chosen:
                resolved.append({"label": label, "name": chosen.name, "domain": chosen.primary_domain,
                                 "company_id": chosen.crustdata_company_id, "match": status})
        ctx.state["competitors"] = resolved
        ctx.event(stage, f"Resolved {len(resolved)} competitors", payload=resolved)


def set_company(ctx: Ctx, c: co.Candidate) -> None:
    ctx.db.update_investigation(
        ctx.inv_id,
        resolved_company_id=c.crustdata_company_id,
        resolved_name=c.name,
        resolved_domain=c.primary_domain,
    )
