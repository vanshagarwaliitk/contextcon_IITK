"""Stage 4 — probe planner: the ONLY free-form agentic loop (Claude tool use, bounded by Python guards).

Claude chooses the next probe; Python validates and executes it. The planner never sees API keys
or raw rows and never writes signals — executors do.
"""
from __future__ import annotations

import json
from typing import Any

from ..llm.client import LLMError, render
from ..providers.crustdata.client import BudgetExceeded, CrustdataError
from . import collect
from .context import Ctx

WINDOW_ENUM = ["recent", "prior", "older"]
_REASON = {"type": "string", "description": "One line (<=20 words) referencing ledger numbers."}


def _tool(name: str, desc: str, extra: dict | None = None) -> dict:
    props: dict[str, Any] = {"theme_id": {"type": "string"}, **(extra or {}), "reason": _REASON}
    return {
        "name": name,
        "description": desc,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
    }


TOOLS = [
    _tool("job_theme_count", "Count theme postings for the company in a time window (cheap, limit:0).",
          {"window": {"type": "string", "enum": WINDOW_ENUM}}),
    _tool("job_theme_rows", "Fetch up to 25 recent theme postings (costs credits per row). Requires recent count >= 2."),
    _tool("job_geo", "Group recent theme postings by country."),
    _tool("job_tech", "Count recent postings mentioning the theme's tech_keywords (technographics filter)."),
    _tool("peer_theme_share", "Theme share of recent postings at each competitor (contrast test)."),
    _tool("people_leadership", "Search current senior employees whose title matches the theme (<=10 results)."),
    _tool("web_evidence", "Web search for public announcements about the company and theme."),
    {
        "name": "stop",
        "description": "Stop probing: evidence collection is complete or budget is nearly exhausted.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {"reason": _REASON}, "required": ["reason"],
                         "additionalProperties": False},
    },
]


def ledger_summary(ctx: Ctx) -> dict:
    out = {}
    for t in ctx.themes():
        led = ctx.ledger_for(t.id)
        out[t.id] = {
            "label": t.label,
            "recent": led["count"].get("recent"),
            "prior": led["count"].get("prior"),
            "older": led["count"].get("older"),
            "rows_fetched": led["rows_fetched"],
            "geo_done": led["geo"] is not None,
            "tech_done": led["tech"] is not None,
            "peers_done": led["peers"] is not None,
            "people_done": led["people"] is not None,
            "web_done": led["web"] is not None,
        }
    return out


class Planner:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.max_steps = ctx.settings.max_probes
        self.steps = ctx.state.get("planner_steps", 0)
        self.done_calls: set[str] = set(ctx.state.get("planner_done_calls", []))
        self.llm_failures = 0

    # ---------------------------------------------------------------- guards + execution
    def _key(self, tool: str, params: dict) -> str:
        return tool + json.dumps({k: v for k, v in params.items() if k != "reason"}, sort_keys=True)

    def _budget_info(self) -> dict:
        b = self.ctx.crust.budget
        return {"calls_left": b.calls_left, "credits_left": round(b.credits_left, 1),
                "steps_used": self.steps, "max_steps": self.max_steps}

    def execute(self, tool: str, params: dict) -> tuple[bool, dict]:
        """Run one probe with all non-negotiable guards. Returns (ok, payload)."""
        reason = params.get("reason", "")
        key = self._key(tool, params)
        if tool not in collect.EXECUTORS:
            return False, {"error": f"unknown tool {tool}"}
        if key in self.done_calls:
            return False, {"error": "duplicate call rejected — this exact probe already ran"}
        if self.steps >= self.max_steps:
            return False, {"error": "MAX_PROBES reached; call stop"}
        b = self.ctx.crust.budget
        if b.calls_left <= 0 or b.credits_left <= 0:
            return False, {"error": "budget exhausted; call stop"}
        if tool == "job_theme_rows" and b.credits_left < 25 and self.ctx.settings.provider_mode == "live":
            return False, {"error": f"only {b.credits_left:.1f} credits left; rows cost up to 25"}
        args = {k: v for k, v in params.items() if k != "reason"}
        try:
            result = collect.EXECUTORS[tool](self.ctx, reason=reason, **args)
        except collect.ProbeRejected as exc:
            self.ctx.event("planner", f"rejected {tool}: {exc}", "warn")
            return False, {"error": f"rejected: {exc}"}
        except BudgetExceeded as exc:
            return False, {"error": f"budget exceeded: {exc}"}
        except CrustdataError as exc:
            self.done_calls.add(key)
            self.steps += 1
            collect.record_probe(self.ctx, tool, args, "failed", f"{reason} | {exc}"[:300])
            self.ctx.warn("probe", f"{tool} failed: {exc}")
            return False, {"error": f"provider error: {str(exc)[:150]}"}
        except TypeError as exc:
            return False, {"error": f"bad arguments: {exc}"}
        self.done_calls.add(key)
        self.steps += 1
        self._persist()
        return True, result

    def _persist(self) -> None:
        self.ctx.state["planner_steps"] = self.steps
        self.ctx.state["planner_done_calls"] = sorted(self.done_calls)
        self.ctx.save()

    # ---------------------------------------------------------------- LLM loop
    def run(self) -> None:
        themes = self.ctx.themes()
        if not themes:
            self.ctx.warn("planner", "no themes to probe")
            return
        system = render("b2_planner.md", {"themes": {t.id: t.label for t in themes}})
        messages: list[dict] = [{"role": "user", "content": json.dumps(
            {"ledger": ledger_summary(self.ctx), "budget": self._budget_info(),
             "competitors": [c["name"] for c in self.ctx.competitors]})}]
        stopped = False
        while not stopped and self.steps < self.max_steps:
            try:
                resp = self.ctx.llm.tool_turn(system, messages, TOOLS)
            except LLMError as exc:
                self.llm_failures += 1
                self.ctx.warn("planner", f"planner LLM error ({self.llm_failures}/2): {exc}")
                if self.llm_failures >= 2:
                    self.ctx.warn("planner", "falling back to deterministic default plan")
                    self.default_plan()
                    return
                continue
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                self.llm_failures += 1
                if self.llm_failures >= 2:
                    self.ctx.warn("planner", "planner stopped calling tools; running deterministic plan")
                    self.default_plan()
                    return
                messages.append({"role": "user", "content": "Call exactly one of the tools (or stop)."})
                continue
            results = []
            for tu in tool_uses:
                params = dict(tu.input or {})
                if tu.name == "stop":
                    self.ctx.event("planner", f"stop — {params.get('reason', '')}")
                    stopped = True
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "stopped"})
                    continue
                self.ctx.event("planner", f"decide {tu.name}({params.get('theme_id', '')}) — {params.get('reason', '')}")
                ok, payload = self.execute(tu.name, params)
                results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "is_error": not ok,
                    "content": json.dumps({"result": payload, "ledger": ledger_summary(self.ctx),
                                           "budget": self._budget_info()}),
                })
            messages.append({"role": "user", "content": results})
            if self.ctx.crust.budget.calls_left <= 0:
                self.ctx.warn("planner", "API call budget exhausted — stopping probes")
                break
        self._persist()

    # ---------------------------------------------------------------- deterministic plan
    def default_plan(self) -> None:
        """count(recent), count(prior), rows(recent) if count>=2, geo, peers; then people + web for top theme."""
        themes = self.ctx.themes()
        for t in themes:
            for w in ("recent", "prior"):
                self.execute("job_theme_count", {"theme_id": t.id, "window": w, "reason": "default plan"})
            led = self.ctx.ledger_for(t.id)
            recent = led["count"].get("recent") or 0
            if recent >= collect.MIN_ROWS_THRESHOLD:
                self.execute("job_theme_rows", {"theme_id": t.id, "reason": "default plan: recent >= 2"})
            if recent > 0:
                self.execute("job_geo", {"theme_id": t.id, "reason": "default plan"})
            if recent >= 3 and self.ctx.competitors:
                self.execute("peer_theme_share", {"theme_id": t.id, "reason": "default plan: contrast"})
        ranked = sorted(themes, key=lambda t: -(self.ctx.ledger_for(t.id)["count"].get("recent") or 0))
        if ranked and (self.ctx.ledger_for(ranked[0].id)["count"].get("recent") or 0) > 0:
            top = ranked[0].id
            if self.ctx.settings.enable_people:
                self.execute("people_leadership", {"theme_id": top, "reason": "default plan: top theme"})
            if self.ctx.settings.enable_web:
                self.execute("web_evidence", {"theme_id": top, "reason": "default plan: top theme"})
        self._persist()

    def coverage_pass(self) -> None:
        """Cheap counts the features/falsifier need (recent/prior/older) for every theme, if missing."""
        for t in self.ctx.themes():
            led = self.ctx.ledger_for(t.id)
            for w in ("recent", "prior", "older"):
                if w not in led["count"]:
                    old_max, self.max_steps = self.max_steps, self.max_steps + 3
                    self.execute("job_theme_count", {"theme_id": t.id, "window": w, "reason": "coverage pass"})
                    self.max_steps = old_max


def run(ctx: Ctx) -> None:
    p = Planner(ctx)
    if ctx.settings.fail_llm:
        ctx.warn("planner", "LLM disabled; using deterministic default plan")
        p.default_plan()
    else:
        p.run()
    p.coverage_pass()
    # test_factors.md constraints, applied automatically (not left to the LLM's choice)
    collect.theme_factor_counts(ctx)
    collect.web_factor_evidence(ctx)
    ctx.event("planner", "Probe loop finished", payload={"ledger": ledger_summary(ctx), **p._budget_info()})
