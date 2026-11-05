"""Stage sequencer with checkpointing (resume) and graceful degradation."""
from __future__ import annotations

import traceback
from typing import Callable

from ..config import Settings, get_settings
from ..db import DB
from ..llm.client import LLM
from ..providers.crustdata.client import CrustdataClient, CrustdataError
from . import brief, cluster, collect, falsify, features, hypothesize, planner, resolve, score, scoping, verify
from .context import Ctx, NeedsConfirmation

STAGES: list[tuple[str, Callable[[Ctx], object]]] = [
    ("resolve", resolve.run),
    ("baseline", collect.baseline),
    ("scoping", scoping.run),
    ("probes", planner.run),
    ("features", features.run),
    ("cluster", cluster.run),
    ("hypothesize", hypothesize.run),
    ("falsify", falsify.run),
    ("score", score.run),
    ("verify", verify.run),
    ("brief", brief.run),
]
CRITICAL = {"resolve"}  # without a resolved company nothing else can run


def run_investigation(
    inv_id: str,
    db: DB | None = None,
    settings: Settings | None = None,
    crust: CrustdataClient | None = None,
    llm: LLM | None = None,
) -> str:
    settings = settings or get_settings()
    db = db or DB(settings.db_path)
    crust = crust or CrustdataClient(db=db, settings=settings)
    llm = llm or LLM(settings=settings, db=db)
    ctx = Ctx.load(db, inv_id, crust, llm, settings)
    # restore budget usage across resumes
    b = ctx.state.get("budget") or {}
    crust.budget.calls = max(crust.budget.calls, b.get("calls", 0))
    crust.budget.credits = max(crust.budget.credits, b.get("credits", 0.0))

    db.update_investigation(inv_id, status="running", provider_mode=ctx.provider_mode)
    ctx.event("runner", f"Investigation started (mode={ctx.provider_mode})")
    failed = False
    for name, fn in STAGES:
        if ctx.state["stages"].get(name) in ("done", "failed"):
            continue
        ctx.event(name, f"Stage {name} started")
        try:
            fn(ctx)
            ctx.state["stages"][name] = "done"
            ctx.event(name, f"Stage {name} done")
        except NeedsConfirmation as exc:
            ctx.save()
            db.update_investigation(inv_id, status="needs_confirmation", error=f"company match {exc}")
            ctx.event("runner", "Paused: confirm the company via POST /api/investigations/{id}/confirm_company", "warn")
            return "needs_confirmation"
        except Exception as exc:  # graceful degradation: record, mark failed, continue
            failed = True
            ctx.state["stages"][name] = "failed"
            msg = f"{type(exc).__name__}: {exc}"
            ctx.warn(name, f"stage failed: {msg[:300]}")
            if not isinstance(exc, CrustdataError):
                ctx.event(name, "traceback", "error", traceback.format_exc()[-2000:])
            if name in CRITICAL:
                ctx.save()
                db.update_investigation(inv_id, status="failed", error=msg[:500])
                return "failed"
        ctx.save()

    status = "completed_with_warnings" if (failed or ctx.warnings) else "completed"
    db.update_investigation(inv_id, status=status)
    ctx.event("runner", f"Investigation {status}", payload=ctx.state.get("budget"))
    return status


def reset_from(db: DB, inv_id: str, stage: str) -> None:
    """Mark a stage and all later stages as not done (used after confirm_company)."""
    from ..db import loads
    import json
    inv = db.get_investigation(inv_id)
    state = loads(inv["stage_state_json"], {}) or {}
    names = [n for n, _ in STAGES]
    for n in names[names.index(stage):]:
        state.get("stages", {}).pop(n, None)
    db.update_investigation(inv_id, stage_state_json=json.dumps(state))
