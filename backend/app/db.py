"""SQLite helpers: WAL mode, migrations on startup, id generation, events."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

_SCHEMA = Path(__file__).with_name("schema.sql")
_lock = threading.RLock()
_initialised: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_settings().db_path
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if path not in _initialised:
        with _lock:
            conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
            conn.commit()
            _initialised.add(path)
    return conn


class DB:
    """Thin wrapper so pipeline code never builds SQL by string concatenation of values."""

    def __init__(self, db_path: str | None = None):
        self.conn = connect(db_path)

    # ---------- generic ----------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with _lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with _lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict) -> None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        self.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})", row.values())

    # ---------- ids ----------
    def next_id(self, investigation_id: str, prefix: str, width: int = 3) -> str:
        with _lock:
            row = self.conn.execute(
                "SELECT value FROM id_counters WHERE investigation_id=? AND prefix=?",
                (investigation_id, prefix),
            ).fetchone()
            value = (row["value"] if row else 0) + 1
            self.conn.execute(
                "INSERT OR REPLACE INTO id_counters (investigation_id, prefix, value) VALUES (?,?,?)",
                (investigation_id, prefix, value),
            )
            self.conn.commit()
        return f"{prefix}_{value:0{width}d}"

    # ---------- investigations ----------
    def create_investigation(
        self,
        company_name: str | None,
        domain: str | None,
        user_question: str | None,
        competitors: list[dict] | None = None,
        provider_mode: str = "live",
    ) -> str:
        inv_id = "inv_" + uuid.uuid4().hex[:10]
        ts = now_iso()
        self.insert(
            "investigations",
            {
                "id": inv_id,
                "company_name": company_name,
                "domain": domain,
                "user_question": user_question,
                "competitors_json": json.dumps(competitors or []),
                "status": "created",
                "provider_mode": provider_mode,
                "stage_state_json": "{}",
                "created_at": ts,
                "updated_at": ts,
            },
        )
        return inv_id

    def get_investigation(self, inv_id: str) -> dict | None:
        return self.one("SELECT * FROM investigations WHERE id=?", (inv_id,))

    def update_investigation(self, inv_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        sets = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE investigations SET {sets} WHERE id=?", [*fields.values(), inv_id])

    # ---------- events ----------
    def emit_event(
        self, investigation_id: str, stage: str, level: str, message: str, payload: Any = None
    ) -> None:
        self.insert(
            "events",
            {
                "investigation_id": investigation_id,
                "stage": stage,
                "level": level,
                "message": message,
                "payload_json": json.dumps(payload, default=str) if payload is not None else None,
                "created_at": now_iso(),
            },
        )

    def events_after(self, investigation_id: str, after_id: int = 0) -> list[dict]:
        return self.query(
            "SELECT * FROM events WHERE investigation_id=? AND id>? ORDER BY id", (investigation_id, after_id)
        )


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
