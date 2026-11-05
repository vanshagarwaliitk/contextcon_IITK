"""Core domain models (pydantic v2) + signal persistence helpers."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .db import DB, loads, now_iso


class SignalType(str, Enum):
    hiring_cluster = "hiring_cluster"
    leadership_change = "leadership_change"
    workforce_movement = "workforce_movement"
    company_change = "company_change"
    web_evidence = "web_evidence"
    derived_metric = "derived_metric"


class EvidenceStatus(str, Enum):
    corroborated = "corroborated"
    single_source = "single_source"
    conflicting = "conflicting"
    unverified = "unverified"
    hypothesis = "hypothesis"


HypothesisType = Literal[
    "new_product_category",
    "new_geography",
    "new_customer_segment",
    "capability_buildout",
    "go_to_market_shift",
    "leadership_or_org_change",
    "contraction_or_cost_cutting",
    "insufficient_evidence",
]
HYPOTHESIS_TYPES: tuple[str, ...] = HypothesisType.__args__  # type: ignore[attr-defined]


class Signal(BaseModel):
    id: str
    investigation_id: str
    signal_type: SignalType
    title: str
    summary: str
    observed_at: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    provider: str = "crustdata"
    endpoint: Optional[str] = None
    raw_ref: Optional[str] = None
    retrieved_at: str = Field(default_factory=now_iso)
    provider_mode: Literal["live", "replay", "seeded"] = "live"
    derived: bool = False
    derived_from: list[str] = Field(default_factory=list)
    computation: Optional[str] = None
    theme_tags: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    status: str = "single_source"  # single_source | weak | corroborated ...

    def compact(self) -> str:
        """One-line representation sent to the LLM (no full descriptions)."""
        f = self.features
        parts = [
            self.id,
            self.signal_type.value,
            self.title[:80],
            (self.observed_at or "")[:10] or "n/a",
            str(f.get("category") or ""),
            str(f.get("country") or ""),
            str(f.get("seniority_level") or ""),
            str(self.source_name or ""),
            ",".join(self.theme_tags),
        ]
        if self.signal_type == SignalType.derived_metric:
            parts.append(f"value={json.dumps(f.get('value'))}")
        return " | ".join(parts)


class Theme(BaseModel):
    id: str
    label: str
    rationale: str
    source: Literal["user_question", "data_driven"]
    title_keywords: list[str]
    description_phrases: list[str]
    anti_keywords: list[str]
    tech_keywords: list[str]
    role_families: list[str]


class Investigation(BaseModel):
    id: str
    company_name: Optional[str] = None
    domain: Optional[str] = None
    resolved_company_id: Optional[int] = None
    user_question: Optional[str] = None
    status: str
    created_at: str


# ---------------------------------------------------------------- persistence


def save_signal(db: DB, sig: Signal) -> Signal:
    db.insert(
        "signals",
        {
            "id": sig.id,
            "investigation_id": sig.investigation_id,
            "signal_type": sig.signal_type.value,
            "title": sig.title,
            "summary": sig.summary,
            "observed_at": sig.observed_at,
            "source_name": sig.source_name,
            "source_url": sig.source_url,
            "provider": sig.provider,
            "endpoint": sig.endpoint,
            "raw_ref": sig.raw_ref,
            "retrieved_at": sig.retrieved_at,
            "provider_mode": sig.provider_mode,
            "derived": int(sig.derived),
            "derived_from_json": json.dumps(sig.derived_from),
            "computation": sig.computation,
            "theme_tags_json": json.dumps(sig.theme_tags),
            "features_json": json.dumps(sig.features, default=str),
            "status": sig.status,
        },
    )
    return sig


def new_signal(db: DB, investigation_id: str, **kwargs: Any) -> Signal:
    sig = Signal(id=db.next_id(investigation_id, "sig"), investigation_id=investigation_id, **kwargs)
    return save_signal(db, sig)


def row_to_signal(row: dict) -> Signal:
    return Signal(
        id=row["id"],
        investigation_id=row["investigation_id"],
        signal_type=row["signal_type"],
        title=row["title"],
        summary=row["summary"],
        observed_at=row["observed_at"],
        source_name=row["source_name"],
        source_url=row["source_url"],
        provider=row["provider"] or "crustdata",
        endpoint=row["endpoint"],
        raw_ref=row["raw_ref"],
        retrieved_at=row["retrieved_at"] or now_iso(),
        provider_mode=row["provider_mode"] or "live",
        derived=bool(row["derived"]),
        derived_from=loads(row["derived_from_json"], []),
        computation=row["computation"],
        theme_tags=loads(row["theme_tags_json"], []),
        features=loads(row["features_json"], {}),
        status=row["status"] or "single_source",
    )


def load_signals(db: DB, investigation_id: str) -> dict[str, Signal]:
    rows = db.query("SELECT * FROM signals WHERE investigation_id=? ORDER BY id", (investigation_id,))
    return {r["id"]: row_to_signal(r) for r in rows}


def load_themes(db: DB, investigation_id: str) -> list[Theme]:
    rows = db.query("SELECT definition_json FROM themes WHERE investigation_id=? ORDER BY id", (investigation_id,))
    return [Theme(**json.loads(r["definition_json"])) for r in rows]


def save_themes(db: DB, investigation_id: str, themes: list[Theme]) -> None:
    db.execute("DELETE FROM themes WHERE investigation_id=?", (investigation_id,))
    for t in themes:
        db.insert(
            "themes",
            {
                "investigation_id": investigation_id,
                "id": t.id,
                "label": t.label,
                "source": t.source,
                "definition_json": t.model_dump_json(),
            },
        )
