"""Stage 6 — clusterer: Python candidates -> LLM grouping/naming (B3) -> Python validator."""
from __future__ import annotations

from ..llm.client import LLMError
from ..llm.schemas import ClusterOut, ClusterResult
from ..models import Signal, SignalType
from .context import Ctx

MIN_POSTINGS = 3
MIN_TYPES = 2


def candidates(signals: dict[str, Signal], theme_id: str) -> tuple[list[Signal], list[Signal]]:
    """(evidence signals tagged with theme, derived metric signals for theme)."""
    ev = [s for s in signals.values() if theme_id in s.theme_tags and not s.derived
          and s.features.get("purpose") != "disconfirm"]
    dm = [s for s in signals.values() if s.derived and theme_id in s.theme_tags]
    return ev, dm


def qualifies(sigs: list[Signal]) -> bool:
    ev = [s for s in sigs if not s.derived]
    postings = sum(1 for s in ev if s.signal_type == SignalType.hiring_cluster)
    types = {s.signal_type for s in ev}
    return postings >= MIN_POSTINGS or len(types) >= MIN_TYPES


def validate_clusters(result: ClusterResult, allowed: set[str]) -> tuple[list[ClusterOut], list[str]]:
    """Drop unknown ids, enforce each signal in <=1 cluster. Returns (clean clusters, notes)."""
    notes, used, clean = [], set(), []
    for c in result.clusters:
        ids = []
        for sid in c.signal_ids:
            if sid not in allowed:
                notes.append(f"dropped unknown id {sid}")
            elif sid in used:
                notes.append(f"dropped duplicate {sid} from {c.cluster_id}")
            else:
                ids.append(sid)
                used.add(sid)
        if ids:
            clean.append(c.model_copy(update={"signal_ids": ids}))
    return clean, notes


def _save(ctx: Ctx, theme_id: str, label: str, why: str, ids: list[str], signals: dict[str, Signal]) -> str:
    cid = ctx.db.next_id(ctx.inv_id, "clu")
    status = "qualifying" if qualifies([signals[i] for i in ids]) else "weak"
    ctx.db.insert("clusters", {"id": cid, "investigation_id": ctx.inv_id, "theme_id": theme_id,
                               "label": label, "why_related": why, "status": status})
    for sid in ids:
        ctx.db.insert("cluster_signals", {"investigation_id": ctx.inv_id, "cluster_id": cid, "signal_id": sid})
    ctx.event("cluster", f"{cid} [{status}] {label} ({len(ids)} signals)", payload=ids)
    return cid


def run(ctx: Ctx) -> list[str]:
    signals = ctx.signals()
    made: list[str] = []
    for theme in ctx.themes():
        ev, dm = candidates(signals, theme.id)
        if not ev:
            continue
        dm_ids = [s.id for s in dm]
        groups: list[tuple[str, str, list[str]]] = []
        if len(ev) >= 2:
            try:
                res = ctx.llm.structured(
                    "b3_cluster.md",
                    {"theme": theme.model_dump(), "signals_compact": "\n".join(s.compact() for s in ev + dm)},
                    ClusterResult, effort="low",
                )
                clean, notes = validate_clusters(res, {s.id for s in ev + dm})
                if notes:
                    ctx.warn("cluster", f"validator: {notes[:5]}")
                for c in clean:
                    ids = [i for i in c.signal_ids if i not in dm_ids]
                    if ids:
                        groups.append((c.label, c.why_related, ids))
            except LLMError as exc:
                ctx.warn("cluster", f"LLM clustering unavailable ({exc}); one cluster per theme")
        if not groups:
            groups = [(theme.label, "All signals matching the theme definition (deterministic grouping).", [s.id for s in ev])]
        # derived metrics always attach to the (largest) cluster they describe
        groups.sort(key=lambda g: -len(g[2]))
        for i, (label, why, ids) in enumerate(groups):
            made.append(_save(ctx, theme.id, label, why, ids + (dm_ids if i == 0 else []), signals))

    qualifying = ctx.db.query("SELECT id FROM clusters WHERE investigation_id=? AND status='qualifying'", (ctx.inv_id,))
    if not qualifying:
        ctx.state["outcome"] = "insufficient_evidence"
        ctx.event("cluster", "No qualifying clusters -> insufficient_evidence outcome (valid result)", "warn")
    else:
        ctx.state["outcome"] = "clusters_found"
    return made


def load_clusters(ctx: Ctx, status: str | None = None) -> list[dict]:
    rows = ctx.db.query("SELECT * FROM clusters WHERE investigation_id=? ORDER BY id", (ctx.inv_id,))
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        r["signal_ids"] = [x["signal_id"] for x in ctx.db.query(
            "SELECT signal_id FROM cluster_signals WHERE investigation_id=? AND cluster_id=? ORDER BY signal_id",
            (ctx.inv_id, r["id"]))]
        out.append(r)
    return out
