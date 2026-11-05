"""Shared investigation context passed to every stage."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import Settings, get_settings
from ..db import DB, loads
from ..llm.client import LLM
from ..models import Signal, Theme, load_signals, load_themes
from ..providers.crustdata.client import CrustdataClient
from ..providers.crustdata.jobs import Window, windows


class NeedsConfirmation(Exception):
    """Company match is ambiguous / needs the user's confirmation; pipeline pauses."""


@dataclass
class Ctx:
    db: DB
    inv_id: str
    crust: CrustdataClient
    llm: LLM
    settings: Settings = field(default_factory=get_settings)
    today: date = field(default_factory=date.today)
    state: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- persistence
    @classmethod
    def load(cls, db: DB, inv_id: str, crust: CrustdataClient, llm: LLM, settings: Settings | None = None) -> "Ctx":
        inv = db.get_investigation(inv_id)
        if inv is None:
            raise KeyError(inv_id)
        ctx = cls(db=db, inv_id=inv_id, crust=crust, llm=llm, settings=settings or get_settings())
        ctx.state = loads(inv["stage_state_json"], {}) or {}
        ctx.state.setdefault("stages", {})
        ctx.state.setdefault("ledger", {})
        ctx.state.setdefault("baseline", {})
        ctx.warnings = ctx.state.setdefault("warnings", [])
        if ctx.state.get("today"):
            ctx.today = date.fromisoformat(ctx.state["today"])
        else:
            ctx.state["today"] = ctx.today.isoformat()
        return ctx

    def save(self) -> None:
        self.state["warnings"] = self.warnings
        self.state["budget"] = {
            "calls": self.crust.budget.calls,
            "credits": round(self.crust.budget.credits, 2),
            "unverified_cost_calls": self.crust.budget.unverified_cost_calls,
            "max_calls": self.crust.budget.max_calls,
            "max_credits": self.crust.budget.max_credits,
        }
        self.state["llm_usage"] = self.llm.usage
        self.db.update_investigation(self.inv_id, stage_state_json=json.dumps(self.state, default=str))

    # ---------------------------------------------------------------- accessors
    @property
    def inv(self) -> dict:
        return self.db.get_investigation(self.inv_id) or {}

    @property
    def company_id(self) -> int | None:
        return self.inv.get("resolved_company_id")

    @property
    def company_domain(self) -> str | None:
        inv = self.inv
        return inv.get("resolved_domain") or inv.get("domain")

    @property
    def company_name(self) -> str | None:
        inv = self.inv
        return inv.get("resolved_name") or inv.get("company_name")

    @property
    def user_question(self) -> str:
        return self.inv.get("user_question") or ""

    @property
    def provider_mode(self) -> str:
        return self.settings.provider_mode if self.settings.provider_mode in ("live", "replay") else "live"

    @property
    def windows(self) -> dict[str, Window]:
        return windows(self.today)

    @property
    def competitors(self) -> list[dict]:
        return self.state.get("competitors", [])

    def themes(self) -> list[Theme]:
        return load_themes(self.db, self.inv_id)

    def theme(self, theme_id: str) -> Theme | None:
        return next((t for t in self.themes() if t.id == theme_id), None)

    def signals(self) -> dict[str, Signal]:
        return load_signals(self.db, self.inv_id)

    def ledger_for(self, theme_id: str) -> dict:
        return self.state["ledger"].setdefault(
            theme_id,
            {
                "count": {},          # window -> int
                "rows_fetched": False,
                "row_signal_ids": [],
                "geo": None,          # list of {key,count}
                "tech": None,
                "peers": None,        # list of {company, theme, total, share}
                "people": None,       # list of signal ids
                "web": None,          # list of signal ids
            },
        )

    # ---------------------------------------------------------------- events
    def event(self, stage: str, message: str, level: str = "info", payload: Any = None) -> None:
        self.db.emit_event(self.inv_id, stage, level, message, payload)

    def warn(self, stage: str, message: str, payload: Any = None) -> None:
        self.warnings.append(f"[{stage}] {message}")
        self.event(stage, message, "warn", payload)
