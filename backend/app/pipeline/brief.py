"""Stage 10b — brief writer (LLM B7 over the claim ledger only) + Jinja2 HTML render + export."""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..db import now_iso
from ..llm.client import LLMError
from ..llm.schemas import BriefOut
from ..models import load_signals
from . import guards
from .context import Ctx
from .falsify import load_tests
from .hypothesize import load_hypotheses
from .verify import load_claims

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
SECTIONS = ["executive_summary", "key_findings", "hypotheses", "counter_evidence_and_alternatives",
            "unknowns", "recommended_next_indicators"]
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))


def validate_brief(b: BriefOut, claims: dict[str, dict]) -> list[str]:
    errs = []
    allowed = guards.allowed_numbers(*[c["text"] for c in claims.values()])
    words = sum(len(s.text.split()) for s in b.executive_summary)
    if words > 130:
        errs.append(f"executive_summary is {words} words (> 120)")
    for sec in SECTIONS:
        for s in getattr(b, sec):
            if not s.claim_ids:
                errs.append(f"{sec}: sentence without claim_ids: '{s.text[:60]}'")
            unknown = [i for i in s.claim_ids if i not in claims]
            if unknown:
                errs.append(f"{sec}: unknown claim ids {unknown}")
            if sec != "unknowns" and any(claims[i]["status"] == "unverified" for i in s.claim_ids if i in claims):
                errs.append(f"{sec}: unverified claims may appear only in unknowns")
            if guards.banned_in(s.text):
                errs.append(f"{sec}: banned phrase {guards.banned_in(s.text)}")
            bad = guards.ungrounded(s.text, allowed)
            if bad:
                errs.append(f"{sec}: numbers {bad} not present in the ledger")
    return errs


def templated_brief(claims: list[dict], outcome: str | None, llm_down: bool) -> dict:
    """Deterministic brief built straight from the ledger (used when the LLM is down or fails)."""
    def sent(c: dict) -> dict:
        return {"text": c["text"], "claim_ids": [c["id"]]}
    facts = [c for c in claims if c["claim_type"] == "fact" and c["status"] == "verified"]
    hyps = [c for c in claims if c["claim_type"] == "hypothesis" and c["section"].startswith("hypothesis:")]
    alts = [c for c in claims if c["section"].startswith("alternative:")]
    unknown = [c for c in claims if c["status"] == "unverified"]
    recs = [c for c in claims if c["claim_type"] == "recommendation"]
    summary = []
    if llm_down:
        summary.append({"text": "Automated reasoning was unavailable for this run; this brief lists observed facts and computed "
                                "features only, with no strategic hypotheses.", "claim_ids": []})
    elif outcome == "insufficient_evidence" or not hyps:
        summary.append({"text": "Insufficient evidence: no signal cluster met the minimum bar for a strategic hypothesis.",
                        "claim_ids": []})
    summary += [sent(c) for c in hyps[:3]]
    return {
        "executive_summary": summary,
        "key_findings": [sent(c) for c in facts if c["section"].startswith(("theme:", "baseline", "company"))][:20],
        "hypotheses": [sent(c) for c in hyps],
        "counter_evidence_and_alternatives": [sent(c) for c in alts],
        "unknowns": [{"text": f"Not verified: {c['text']}", "claim_ids": [c["id"]]} for c in unknown],
        "recommended_next_indicators": [sent(c) for c in recs],
        "writer": "templated",
    }


def run(ctx: Ctx) -> dict:
    claims = load_claims(ctx)
    by_id = {c["id"]: c for c in claims}
    coverage = ctx.state.get("coverage", {})
    inv = ctx.inv
    scope = {
        "company": ctx.company_name, "domain": ctx.company_domain, "crustdata_company_id": ctx.company_id,
        "question": ctx.user_question or None, "generated_at": now_iso(), "provider_mode": ctx.provider_mode,
        "competitors": [c["name"] for c in ctx.competitors],
        "windows": {k: [str(w.start), str(w.end)] for k, w in ctx.windows.items()},
        "sources": ["Crustdata Company Identify/Enrich", "Crustdata Job Search"]
                   + (["Crustdata Person Search"] if ctx.settings.enable_people else [])
                   + (["Crustdata Web Search"] if ctx.settings.enable_web else []),
    }
    sections: dict
    llm_down = bool(ctx.state.get("llm_down")) or ctx.settings.fail_llm
    if llm_down:
        sections = templated_brief(claims, ctx.state.get("outcome"), True)
    else:
        try:
            ledger_txt = "\n".join(f"{c['id']} | {c['claim_type']} | {c['status']} | {c['text']} | {','.join(c['signal_ids'])}"
                                   for c in claims)
            b = ctx.llm.structured("b7_brief.md", {"claims": ledger_txt, "scope": scope, "coverage": coverage},
                                   BriefOut, validator=lambda r: validate_brief(r, by_id))
            sections = {k: [s.model_dump() for s in getattr(b, k)] for k in SECTIONS}
            sections["writer"] = "llm"
        except LLMError as exc:
            ctx.warn("brief", f"LLM brief writer failed ({exc}); using templated brief")
            sections = templated_brief(claims, ctx.state.get("outcome"), False)

    signals = load_signals(ctx.db, ctx.inv_id)
    hyps = load_hypotheses(ctx)
    for h in hyps:
        h["tests"] = load_tests(ctx, h["id"])
    report = {
        "scope": scope,
        "coverage": coverage,
        "outcome": ctx.state.get("outcome"),
        "sections": sections,
        "claims": claims,
        "hypotheses": hyps,
        "signals": [s.model_dump(mode="json") for s in signals.values()],
        "warnings": ctx.warnings,
        "budget": ctx.state.get("budget", {}),
        "status_note": "Hypotheses are interpretations of public signals, not predictions or facts.",
        "profile_missing": not inv.get("profile_json"),
    }
    ctx.db.update_investigation(ctx.inv_id, report_json=json.dumps(report, default=str))
    ctx.event("brief", f"Brief ready ({sections.get('writer')} writer)")
    return report


def render_html(report: dict) -> str:
    signals = {s["id"]: s for s in report["signals"]}
    claims = {c["id"]: c for c in report["claims"]}
    timeline = sorted((s for s in report["signals"] if not s["derived"]),
                      key=lambda s: s.get("observed_at") or "", reverse=True)
    return _env.get_template("brief.html").render(r=report, signals=signals, claims=claims, timeline=timeline, json=json)


def export_pdf(html: str) -> bytes | None:
    try:
        from weasyprint import HTML  # optional dependency
    except Exception:
        return None
    try:
        return HTML(string=html).write_pdf()
    except Exception:
        return None
