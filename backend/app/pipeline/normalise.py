"""Stage 5a — normaliser: provider records -> Signal rows. Deterministic templates only (no LLM text)."""
from __future__ import annotations

import re

from ..models import Signal, SignalType, Theme, new_signal, save_signal
from ..providers.crustdata.jobs import get_path
from ..providers.crustdata.web import classify_source
from .context import Ctx

# Seniority tiers: 0 intern/junior < 1 mid < 2 senior < 3 lead/staff/principal < 4 manager/director/head/vp/chief
_TIERS = [
    (4, re.compile(r"\b(chief|c[etor]o|vp|vice president|head|director|manager|gm|general manager)\b", re.I)),
    (3, re.compile(r"\b(lead|staff|principal|architect|distinguished)\b", re.I)),
    (2, re.compile(r"\b(senior|sr\.?|iii|iv)\b", re.I)),
    (0, re.compile(r"\b(intern|internship|junior|jr\.?|trainee|graduate|apprentice|entry[- ]level)\b", re.I)),
]
TIER_NAMES = {0: "junior", 1: "mid", 2: "senior", 3: "lead", 4: "leadership"}


def seniority_tier(title: str | None) -> int:
    t = title or ""
    for tier, rx in _TIERS:
        if rx.search(t):
            return tier
    return 1


def theme_hits(text: str, theme: Theme) -> bool:
    blob = text.lower()
    terms = [*theme.title_keywords, *theme.description_phrases]
    return any(term.lower() in blob for term in terms if term.strip())


def job_rows_to_signals(ctx: Ctx, rows: list[dict], theme: Theme, probe_id: str, endpoint: str = "/job/search") -> list[Signal]:
    out: list[Signal] = []
    existing = {s.features.get("job_id"): s for s in ctx.signals().values() if s.features.get("job_id")}
    for r in rows:
        title = get_path(r, "job_details.title") or "Untitled posting"
        job_id = r.get("crustdata_job_id")
        if job_id in existing:  # same posting already a signal (another theme) -> add tag
            sig = existing[job_id]
            if theme.id not in sig.theme_tags:
                sig.theme_tags.append(theme.id)
                save_signal(ctx.db, sig)
            out.append(sig)
            continue
        loc = get_path(r, "location.raw") or ", ".join(
            x for x in (get_path(r, "location.city"), get_path(r, "location.country")) if x
        ) or "unknown location"
        cat = get_path(r, "job_details.category") or "n/a"
        added = get_path(r, "metadata.date_added")
        source = get_path(r, "job_details.source") or "unknown"
        tier = seniority_tier(title)
        tags = [theme.id]
        for other in ctx.themes():
            if other.id != theme.id and theme_hits(f"{title} {get_path(r, 'content.description') or ''}", other):
                tags.append(other.id)
        sig = new_signal(
            ctx.db,
            ctx.inv_id,
            signal_type=SignalType.hiring_cluster,
            title=title,
            summary=f"Posting '{title}' in {loc} (category: {cat}), added {(added or 'n/a')[:10]} on {source}",
            observed_at=added,
            source_name=source,
            source_url=get_path(r, "job_details.url"),
            endpoint=endpoint,
            raw_ref=f"probe:{probe_id}#job:{job_id}",
            provider_mode=ctx.provider_mode,
            theme_tags=tags,
            features={
                "job_id": job_id,
                "category": cat,
                "country": get_path(r, "location.country"),
                "city": get_path(r, "location.city"),
                "seniority_tier": tier,
                "seniority_level": TIER_NAMES[tier],
                "reposted": bool(get_path(r, "job_details.reposted_job")),
                "dup_key": r.get("_dup_key"),
                "norm_title": r.get("_norm_title"),
                "desc_hash": r.get("_desc_hash"),
                "workplace_type": get_path(r, "job_details.workplace_type"),
            },
        )
        existing[job_id] = sig
        out.append(sig)
    return out


def people_to_signals(ctx: Ctx, people: list[dict], theme: Theme, probe_id: str) -> list[Signal]:
    out = []
    for p in people:
        if not p.get("name") and not p.get("title"):
            continue
        start = p.get("start_date")
        start_txt = f"started {str(start)[:10]}" if start else "start date not available"
        out.append(
            new_signal(
                ctx.db,
                ctx.inv_id,
                signal_type=SignalType.leadership_change,
                title=f"{p.get('title') or 'Senior role'} at {p.get('employer') or ctx.company_name}",
                summary=f"Public profile lists {p.get('name') or 'a person'} as '{p.get('title')}' at "
                        f"{p.get('employer') or ctx.company_name} ({start_txt}).",
                observed_at=str(start) if start else None,
                source_name="crustdata_person_search",
                source_url=p.get("profile_url"),
                endpoint="/person/search",
                raw_ref=f"probe:{probe_id}#person:{p.get('person_id')}",
                provider_mode=ctx.provider_mode,
                theme_tags=[theme.id],
                features={
                    "seniority_tier": seniority_tier(p.get("title")),
                    "seniority_level": p.get("seniority_level") or TIER_NAMES[seniority_tier(p.get("title"))],
                },
            )
        )
    return out


def web_to_signals(ctx: Ctx, results: list[dict], theme: Theme, probe_id: str, purpose: str = "support",
                   web_factor: str | None = None) -> list[Signal]:
    out = []
    for r in results:
        cls = classify_source(r.get("url"), ctx.company_domain)
        out.append(
            new_signal(
                ctx.db,
                ctx.inv_id,
                signal_type=SignalType.web_evidence,
                title=(r.get("title") or r.get("url") or "")[:200],
                summary=f"Web result ({cls} source): '{(r.get('title') or '')[:120]}' — {r.get('snippet') or 'no snippet'}",
                observed_at=r.get("date"),
                source_name=cls,
                source_url=r.get("url"),
                endpoint="/web/search/live",
                raw_ref=f"probe:{probe_id}",
                provider_mode=ctx.provider_mode,
                theme_tags=[theme.id],
                features={"source_class": cls, "purpose": purpose,
                          **({"web_factor": web_factor} if web_factor else {})},
            )
        )
    return out
