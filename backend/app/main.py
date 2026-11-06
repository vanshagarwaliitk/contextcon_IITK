"""FastAPI app — PRD 8.3 routes + SSE events + confirm_company + watchlist + static UI."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, SecretStr

from .config import Settings, get_settings
from .db import DB, loads, now_iso
from .llm.claude_cli import find_cli, resolve_backend
from .models import load_signals
from .pipeline import brief as brief_mod
from .pipeline.runner import reset_from, run_investigation
from .providers.crustdata.jobs import windows as job_windows
from datetime import date

STATIC = Path(__file__).with_name("static")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
app = FastAPI(title="CoreData", version="0.1.0")
_running: set[str] = set()


def db() -> DB:
    return DB(get_settings().db_path)


class CreateInvestigation(BaseModel):
    company_name: Optional[str] = None
    domain: Optional[str] = None
    user_question: Optional[str] = None
    competitors: list[str] = []
    auto_run: bool = True
    crustdata_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None


class ConfirmCompany(BaseModel):
    crustdata_company_id: Optional[int] = None
    domain: Optional[str] = None  # correct the input instead of picking a candidate
    crustdata_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None


class RunInvestigation(BaseModel):
    crustdata_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None


class WatchItem(BaseModel):
    item_type: str  # hypothesis | signal
    item_id: str
    note: Optional[str] = None


def _get(inv_id: str) -> dict:
    inv = db().get_investigation(inv_id)
    if not inv:
        raise HTTPException(404, "investigation not found")
    return inv


def _settings_for_request(body: CreateInvestigation | ConfirmCompany | RunInvestigation) -> Settings:
    settings = get_settings()
    crustdata_key = body.crustdata_api_key.get_secret_value() if body.crustdata_api_key else settings.crustdata_api_key
    anthropic_key = body.anthropic_api_key.get_secret_value() if body.anthropic_api_key else settings.anthropic_api_key
    if settings.provider_mode == "live" and not crustdata_key.strip():
        raise HTTPException(400, "Crustdata API key is required in live mode")
    anthropic_key = anthropic_key.strip()
    if anthropic_key.startswith("AIza"):
        raise HTTPException(400, "That's a Google Gemini key. CoreData's reasoning runs on Claude: use an Anthropic key "
                                 "(sk-ant-…), or leave the field empty to use your logged-in Claude account.")
    if anthropic_key and not anthropic_key.startswith("sk-ant-"):
        raise HTTPException(400, "Anthropic API keys start with sk-ant-. Leave the field empty to use your Claude account.")
    out = replace(settings, crustdata_api_key=crustdata_key, anthropic_api_key=anthropic_key)
    if settings.provider_mode == "live" and resolve_backend(out) == "none":
        raise HTTPException(400, "Claude isn't available: enter an Anthropic API key, or install Claude Code and log in "
                                 "with your Claude account.")
    return out


def _start(inv_id: str, settings: Settings | None = None) -> bool:
    if inv_id in _running:
        return False
    _running.add(inv_id)

    def work() -> None:
        try:
            run_investigation(inv_id, settings=settings)
        finally:
            _running.discard(inv_id)

    threading.Thread(target=work, daemon=True).start()
    return True


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "provider_mode": s.provider_mode, "model": s.llm_model,
            "crustdata_key": bool(s.crustdata_api_key), "anthropic_key": bool(s.anthropic_api_key),
            "llm_backend": resolve_backend(s), "claude_account": bool(find_cli(s)),
            "people": s.enable_people, "web": s.enable_web, "factors": s.enable_factors}


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    """React build (frontend/dist) when present, else the zero-build fallback page."""
    built = FRONTEND_DIST / "index.html"
    return FileResponse(built if built.exists() else STATIC / "index.html")


@app.get("/assets/{path:path}")
def assets(path: str) -> FileResponse:
    f = (FRONTEND_DIST / "assets" / path).resolve()
    if not str(f).startswith(str((FRONTEND_DIST / "assets").resolve())) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@app.get("/api/investigations/{inv_id}/insights")
def insights(inv_id: str) -> dict:
    """Chart data: themes, computed features, per-theme ledger (counts, geo, peers), budget, emerging terms."""
    inv = _get(inv_id)
    state = loads(inv["stage_state_json"], {}) or {}
    themes = [loads(r["definition_json"], {}) for r in db().query(
        "SELECT definition_json FROM themes WHERE investigation_id=? ORDER BY id", (inv_id,))]
    ledger = state.get("ledger", {})
    base = state.get("baseline", {})
    return {
        "themes": themes,
        "features": state.get("features", {}),
        "ledger": {tid: {k: v for k, v in led.items() if k not in ("count_bodies",)} for tid, led in ledger.items()},
        "baseline_totals": {w: (base.get(f"{w}:count") or {}).get("value") for w in ("recent", "prior", "older")},
        "windows": {k: {"start": str(w.start), "end": str(w.end), "days": w.days}
                    for k, w in job_windows(date.fromisoformat(state["today"]) if state.get("today") else None).items()},
        "emerging_terms": state.get("emerging_terms", []),
        "competitors": state.get("competitors", []),
        "budget": state.get("budget", {}),
        "llm_usage": state.get("llm_usage", {}),
        "outcome": state.get("outcome"),
    }


@app.post("/api/investigations")
def create(body: CreateInvestigation) -> dict:
    if not (body.company_name or body.domain):
        raise HTTPException(400, "company_name or domain is required")
    s = _settings_for_request(body)
    inv_id = db().create_investigation(body.company_name, body.domain, body.user_question,
                                       body.competitors, provider_mode=s.provider_mode)
    if body.auto_run:
        _start(inv_id, s)
    return {"id": inv_id, "status": "running" if body.auto_run else "created"}


@app.get("/api/investigations")
def list_investigations() -> list[dict]:
    return db().query("SELECT id, company_name, domain, resolved_name, user_question, status, created_at "
                      "FROM investigations ORDER BY created_at DESC LIMIT 50")


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str) -> dict:
    inv = _get(inv_id)
    state = loads(inv["stage_state_json"], {}) or {}
    return {
        "id": inv["id"], "company_name": inv["company_name"], "domain": inv["domain"],
        "resolved_company_id": inv["resolved_company_id"], "resolved_name": inv["resolved_name"],
        "resolved_domain": inv["resolved_domain"], "user_question": inv["user_question"],
        "status": inv["status"], "provider_mode": inv["provider_mode"], "created_at": inv["created_at"],
        "stages": state.get("stages", {}), "budget": state.get("budget", {}), "coverage": state.get("coverage"),
        "outcome": state.get("outcome"), "warnings": state.get("warnings", []),
        "candidates": loads(inv["candidates_json"], []), "competitors": state.get("competitors", []),
        "running": inv_id in _running, "error": inv["error"],
    }


@app.post("/api/investigations/{inv_id}/run")
def run(inv_id: str, body: RunInvestigation | None = None) -> dict:
    inv = _get(inv_id)
    if inv["status"] == "needs_confirmation":
        raise HTTPException(409, "confirm the company first")
    started = _start(inv_id, _settings_for_request(body or RunInvestigation()))
    return {"id": inv_id, "started": started}


@app.post("/api/investigations/{inv_id}/confirm_company")
def confirm_company(inv_id: str, body: ConfirmCompany) -> dict:
    inv = _get(inv_id)
    settings = _settings_for_request(body)
    d = db()
    cands = loads(inv["candidates_json"], [])
    if body.crustdata_company_id is not None:
        chosen = next((c for c in cands if c["crustdata_company_id"] == body.crustdata_company_id), None)
        if chosen is None:
            raise HTTPException(400, "id is not one of the candidates; correct the domain instead")
        d.update_investigation(inv_id, resolved_company_id=chosen["crustdata_company_id"],
                               resolved_name=chosen.get("name"), resolved_domain=chosen.get("primary_domain"),
                               status="created", error=None)
    elif body.domain:
        d.update_investigation(inv_id, domain=body.domain, resolved_company_id=None, status="created", error=None)
    else:
        raise HTTPException(400, "give crustdata_company_id or domain")
    reset_from(d, inv_id, "resolve")
    d.emit_event(inv_id, "resolve", "info", "Company confirmed by user")
    _start(inv_id, settings)
    return {"id": inv_id, "started": True}


@app.get("/api/investigations/{inv_id}/signals")
def signals(inv_id: str) -> list[dict]:
    _get(inv_id)
    return [s.model_dump(mode="json") for s in load_signals(db(), inv_id).values()]


@app.get("/api/investigations/{inv_id}/hypotheses")
def hypotheses(inv_id: str) -> list[dict]:
    inv = _get(inv_id)
    report = loads(inv["report_json"])
    if report:
        return report["hypotheses"]
    return db().query("SELECT * FROM hypotheses WHERE investigation_id=? ORDER BY id", (inv_id,))


@app.get("/api/investigations/{inv_id}/report")
def report(inv_id: str) -> dict:
    inv = _get(inv_id)
    r = loads(inv["report_json"])
    if not r:
        raise HTTPException(404, "report not ready")
    return r


@app.get("/api/investigations/{inv_id}/export")
def export(inv_id: str, format: str = "pdf") -> Response:
    inv = _get(inv_id)
    r = loads(inv["report_json"])
    if not r:
        raise HTTPException(404, "report not ready")
    html = brief_mod.render_html(r)
    name = f"coredata_{(inv['resolved_name'] or inv['company_name'] or inv_id).replace(' ', '_')}"
    if format == "pdf":
        pdf = brief_mod.export_pdf(html)
        if pdf:
            return Response(pdf, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return Response(html, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{name}.html"'})


@app.get("/api/investigations/{inv_id}/brief", response_class=HTMLResponse)
def brief_html(inv_id: str) -> HTMLResponse:
    inv = _get(inv_id)
    r = loads(inv["report_json"])
    if not r:
        raise HTTPException(404, "report not ready")
    return HTMLResponse(brief_mod.render_html(r))


@app.get("/api/investigations/{inv_id}/events")
async def events(inv_id: str, request: Request, after: int = 0) -> StreamingResponse:
    _get(inv_id)

    async def stream():
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            rows = db().events_after(inv_id, last)
            for e in rows:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            inv = db().get_investigation(inv_id) or {}
            done = inv.get("status") in ("completed", "completed_with_warnings", "failed", "needs_confirmation")
            if done and not rows and inv_id not in _running:
                idle += 1
                if idle > 2:
                    yield f"event: end\ndata: {json.dumps({'status': inv.get('status')})}\n\n"
                    break
            await asyncio.sleep(0.7)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/investigations/{inv_id}/api_logs")
def api_logs(inv_id: str) -> list[dict]:
    return db().query("SELECT endpoint, label, status_code, credits_estimate, error, latency_ms, cache_hit, created_at "
                      "FROM api_logs WHERE investigation_id=? ORDER BY id", (inv_id,))


@app.post("/api/investigations/{inv_id}/watchlist")
def add_watch(inv_id: str, item: WatchItem) -> dict:
    _get(inv_id)
    db().insert("watchlist", {"investigation_id": inv_id, "item_type": item.item_type, "item_id": item.item_id,
                              "note": item.note, "created_at": now_iso()})
    return {"ok": True}


@app.get("/api/investigations/{inv_id}/watchlist")
def get_watch(inv_id: str) -> list[dict]:
    return db().query("SELECT * FROM watchlist WHERE investigation_id=? ORDER BY id", (inv_id,))
