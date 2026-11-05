"""Stage 1 — scoping (LLM B1) with Python-enforced rules + deterministic fallback."""
from __future__ import annotations

import json
import re

from ..llm.client import LLMError
from ..llm.schemas import ScopingResult
from ..models import Theme, save_themes
from .context import Ctx

MAX_THEMES = 5
GENERIC = {"ai", "data", "innovation", "technology", "tech", "software", "digital", "cloud", "platform",
           "solutions", "growth", "new", "product", "engineering", "team", "business", "ml"}
_QSTOP = set("are is they them their moving into expanding expand entering enter towards toward the a an "
             "in on of to do does company competitor what whether any new more about building build".split())


def _generic(phrase: str) -> bool:
    return phrase.strip().lower() in GENERIC or len(phrase.strip()) < 3


def validate(result: ScopingResult, emerging: list[dict]) -> list[str]:
    errs = []
    if not result.themes:
        errs.append("no themes returned")
    if len(result.themes) > MAX_THEMES:
        errs.append(f"{len(result.themes)} themes > max {MAX_THEMES}")
    if emerging and not any(t.source == "data_driven" for t in result.themes):
        errs.append("emerging_terms is non-empty but no theme has source='data_driven'")
    ids = [t.id for t in result.themes]
    if len(set(ids)) != len(ids):
        errs.append("duplicate theme ids")
    for t in result.themes:
        bad = [p for p in t.description_phrases if _generic(p)]
        if bad:
            errs.append(f"theme {t.id}: generic description_phrases not allowed: {bad}")
        if not t.description_phrases and not t.title_keywords:
            errs.append(f"theme {t.id}: needs title_keywords or description_phrases")
    return errs


def clean(result: ScopingResult) -> list[Theme]:
    themes = []
    for i, t in enumerate(result.themes[:MAX_THEMES], start=1):
        t = t.model_copy(update={
            "id": f"t{i}",
            "description_phrases": [p for p in t.description_phrases if not _generic(p)][:8],
            "title_keywords": [k for k in t.title_keywords if k.strip()][:8],
        })
        themes.append(t)
    return themes


def fallback_themes(question: str, emerging: list[dict]) -> list[Theme]:
    """Deterministic themes when the LLM is unavailable: question keywords + top emerging terms."""
    themes: list[Theme] = []
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]+", question.lower()) if w not in _QSTOP and len(w) > 3]
    if words:
        kw = words[:3]
        themes.append(Theme(id="t1", label=" ".join(kw), rationale="Keywords taken from the user question.",
                            source="user_question", title_keywords=kw, description_phrases=[" ".join(kw)] if len(kw) > 1 else kw,
                            anti_keywords=["robotic process automation"] if "robotics" in kw else [],
                            tech_keywords=[], role_families=["engineering"]))
    for term in emerging[:2]:
        themes.append(Theme(id=f"t{len(themes) + 1}", label=term["term"],
                            rationale="Title term rising in recent vs prior postings (computed by code).",
                            source="data_driven", title_keywords=[term["term"]], description_phrases=[term["term"]],
                            anti_keywords=[], tech_keywords=[], role_families=[]))
    return themes


def run(ctx: Ctx) -> list[Theme]:
    emerging = ctx.state.get("emerging_terms", [])
    profile = ctx.inv.get("profile_json") or ""
    try:
        profile_short = json.dumps(json.loads(profile).get("basic_info", {}), default=str)[:3000] if profile else "{}"
    except ValueError:
        profile_short = "{}"
    variables = {
        "company_profile": profile_short or json.dumps({"name": ctx.company_name, "domain": ctx.company_domain}),
        "competitors": [c["name"] for c in ctx.competitors],
        "user_question": ctx.user_question or "(none)",
        "emerging_terms": emerging,
    }
    try:
        result = ctx.llm.structured("b1_scoping.md", variables, ScopingResult, validator=lambda r: validate(r, emerging))
        themes = clean(result)
        ctx.event("scoping", f"LLM defined {len(themes)} themes", payload=[t.model_dump() for t in themes])
    except LLMError as exc:
        ctx.warn("scoping", f"LLM scoping unavailable ({exc}); using deterministic themes")
        themes = fallback_themes(ctx.user_question, emerging)
    if not themes:
        ctx.warn("scoping", "No themes could be defined (no question and no emerging terms)")
    save_themes(ctx.db, ctx.inv_id, themes)
    return themes
