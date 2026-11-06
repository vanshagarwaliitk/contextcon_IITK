"""CoreData guided wizard: asks a few questions, runs the investigation, opens the brief.

Started by double-clicking CoreData.bat (Windows) or coredata.sh (Mac/Linux). No flags needed.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import threading
import time
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import _path  # noqa: F401

from app.config import ROOT, Settings, get_settings
from app.db import DB, loads
from app.llm import claude_cli
from app.pipeline.brief import render_html
from app.pipeline.runner import reset_from, run_investigation

EXPORTS = ROOT / "exports"
ENV_FILE = ROOT / ".env"
EXAMPLES = [
    "Are they moving into robotics?",
    "Are they expanding into Europe?",
    "Are they building an AI product team?",
    "Are they moving upmarket to enterprise?",
]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ------------------------------------------------------------------ console helpers
def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str = "") -> None:
    say("\n" + ("-- " + title + " " if title else "") + "-" * max(4, 60 - len(title)))


def ask(prompt: str, default: str = "", required: bool = False, validate: Callable[[str], str | None] | None = None,
        secret: bool = False, input_fn: Callable[[str], str] = input) -> str:
    """Ask until the answer is valid. validate() returns an error message or None."""
    hint = f" [{default}]" if default and not secret else ""
    while True:
        raw = (getpass.getpass if secret and input_fn is input else input_fn)(f"{prompt}{hint}: ").strip()
        value = raw or default
        if required and not value:
            say("  Please enter a value.")
            continue
        err = validate(value) if (validate and value) else None
        if err:
            say(f"  {err}")
            continue
        return value


def yes(prompt: str, default: bool = False, input_fn: Callable[[str], str] = input) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input_fn(f"{prompt} ({d}): ").strip().lower()
    return default if not raw else raw.startswith("y")


def mask(key: str) -> str:
    return f"{key[:7]}...{key[-4:]}" if len(key) > 12 else "set"


# ------------------------------------------------------------------ key handling
def check_anthropic(key: str) -> str | None:
    if key.startswith("AIza"):
        return "That looks like a Google Gemini key. CoreData's reasoning uses Claude, so it needs an Anthropic key (starts with sk-ant-)."
    if key.startswith("sk-") and not key.startswith("sk-ant-"):
        return "That looks like an OpenAI key. CoreData needs an Anthropic key (starts with sk-ant-)."
    if not key.startswith("sk-ant-"):
        return "Anthropic keys start with sk-ant-. Check the key and try again."
    return None


def save_to_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    for k, v in values.items():
        pat = re.compile(rf"^\s*{k}\s*=")
        for i, line in enumerate(lines):
            if pat.match(line):
                lines[i] = f"{k}={v}"
                break
        else:
            lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_keys(settings: Settings, input_fn: Callable[[str], str] = input) -> Settings:
    rule("API keys")
    if settings.provider_mode != "live":
        say(f"Provider mode is '{settings.provider_mode}': recorded Crustdata data is used, so no Crustdata key is needed.")
    anthropic_key = settings.anthropic_api_key
    use_account = False
    if settings.llm_backend in ("claude_cli", "cli", "account") and claude_cli.find_cli(settings):
        use_account = True
        anthropic_key = ""
        say("Claude: using your logged-in Claude account (LLM_BACKEND=claude_cli in .env).")
    elif anthropic_key and not check_anthropic(anthropic_key):
        say(f"Anthropic key: using the one in .env ({mask(anthropic_key)})")
    else:
        if anthropic_key:
            say(f"The Anthropic key in .env doesn't look right: {check_anthropic(anthropic_key)}")
        say("Claude is CoreData's reasoning engine.")
        if claude_cli.find_cli(settings) and settings.llm_backend != "api":
            say("Claude Code is installed on this computer, so CoreData can use your Claude account instead of an API key.")
            say("(Usage counts against your Claude plan limits.)")
            use_account = yes("Use your logged-in Claude account?", default=True, input_fn=input_fn)
        if use_account:
            anthropic_key = ""
            use_account = check_account(settings)
        if not use_account:
            say("Without Claude you get facts only, no hypotheses.")
            anthropic_key = ask("Anthropic API key (sk-ant-..., Enter to skip)", secret=True, validate=check_anthropic,
                                input_fn=input_fn)
            if not anthropic_key:
                say("  Skipping: this run will produce facts and computed numbers only.")
    crust_key = settings.crustdata_api_key
    if settings.provider_mode == "live":
        if crust_key:
            say(f"Crustdata key: using the one in .env ({mask(crust_key)})")
        else:
            crust_key = ask("Crustdata API key", secret=True, required=True, input_fn=input_fn)
    new = {}
    if anthropic_key and anthropic_key != settings.anthropic_api_key:
        new["ANTHROPIC_API_KEY"] = anthropic_key
    if crust_key and crust_key != settings.crustdata_api_key:
        new["CRUSTDATA_API_KEY"] = crust_key
    if use_account and settings.llm_backend not in ("claude_cli", "cli", "account"):
        new["LLM_BACKEND"] = "claude_cli"
    if new and yes("Save these settings to the .env file so you aren't asked next time?", input_fn=input_fn):
        save_to_env(new)
        say("  Saved to .env (this file is git-ignored).")
    fail_llm = settings.fail_llm or not (anthropic_key or use_account)
    return replace(settings, anthropic_api_key=anthropic_key, crustdata_api_key=crust_key, fail_llm=fail_llm,
                   llm_backend="claude_cli" if use_account else ("api" if anthropic_key else settings.llm_backend))


def check_account(settings: Settings) -> bool:
    """One tiny structured call to confirm the Claude account login works before the investigation starts."""
    say("  Checking your Claude login (a few seconds)...")
    try:
        data, meta = claude_cli.run_structured(
            settings, "Reply with ok=true.",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
            "low")
    except claude_cli.ClaudeCLIError as exc:
        say(f"  That didn't work: {exc}")
        say("  To log in: open a terminal, run `claude`, and sign in with your Claude account.")
        return False
    say(f"  Claude account works ({settings.llm_model}).")
    return bool(data.get("ok", True))


# ------------------------------------------------------------------ investigation inputs
def collect_target(input_fn: Callable[[str], str] = input) -> dict:
    rule("Who are we investigating?")
    target = ask("Competitor website or company name (e.g. microsoft.com)", required=True, input_fn=input_fn)
    is_domain = "." in target and " " not in target
    say("\nWhat do you want to know? CoreData answers 'what might they be preparing to do?' questions.")
    for i, q in enumerate(EXAMPLES, 1):
        say(f"  {i}. {q}")
    q = ask("Type a question, a number above, or press Enter for a data-driven scan", input_fn=input_fn)
    if q.isdigit() and 1 <= int(q) <= len(EXAMPLES):
        q = EXAMPLES[int(q) - 1]
    peers = ask("Peer companies to compare against (comma-separated, Enter for none)", input_fn=input_fn)
    return {
        "domain": target if is_domain else None,
        "company_name": None if is_domain else target,
        "question": q or None,
        "competitors": [p.strip() for p in peers.split(",") if p.strip()],
    }


# ------------------------------------------------------------------ live run
def describe(e: dict) -> str | None:
    m, st = e["message"], e["stage"]
    if re.match(r"^Stage \w+ started$", m) or m == "traceback":
        return None
    if re.match(r"^Stage \w+ done$", m):
        return f"  [done] {st}"
    if st == "planner" and m.startswith("decide "):
        return f"  [Claude] chose {m[7:]}"
    if st == "probe":
        return f"  [Crustdata] {m}"
    if e["level"] != "info":
        return f"  [warning] {m[:180]}"
    return f"  [{st}] {m[:180]}"


def run_live(db: DB, inv_id: str, settings: Settings, runner: Callable[..., str] = run_investigation, **kw) -> str:
    result: dict = {}

    def work() -> None:
        try:
            result["status"] = runner(inv_id, db=db, settings=settings, **kw)
        except Exception as exc:  # never leave the wizard hanging
            result["status"] = "failed"
            result["error"] = str(exc)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    last = 0
    while True:
        alive = t.is_alive()
        for e in db.events_after(inv_id, last):  # print new events, then a final drain after the run ends
            last = e["id"]
            line = describe(e)
            if line:
                say(line)
        if not alive:
            break
        time.sleep(0.4)
    if result.get("error"):
        say(f"  [error] {result['error']}")
    return result.get("status", "failed")


def confirm_company(db: DB, inv_id: str, input_fn: Callable[[str], str] = input) -> bool:
    inv = db.get_investigation(inv_id)
    cands = loads(inv["candidates_json"], []) or []
    rule("Which company did you mean?")
    for i, c in enumerate(cands, 1):
        say(f"  {i}. {c.get('name')}  ({c.get('primary_domain') or 'no domain'}, id {c['crustdata_company_id']})")
    ans = ask("Pick a number, or type the company's website instead", required=True, input_fn=input_fn)
    if ans.isdigit() and 1 <= int(ans) <= len(cands):
        c = cands[int(ans) - 1]
        db.update_investigation(inv_id, resolved_company_id=c["crustdata_company_id"], resolved_name=c.get("name"),
                                resolved_domain=c.get("primary_domain"), status="created", error=None)
    else:
        db.update_investigation(inv_id, domain=ans, company_name=None, resolved_company_id=None, status="created", error=None)
    reset_from(db, inv_id, "resolve")
    db.emit_event(inv_id, "resolve", "info", "Company confirmed by user")
    return True


# ------------------------------------------------------------------ results
def summarise(db: DB, inv_id: str, status: str, open_browser: bool = True) -> Path | None:
    row = db.get_investigation(inv_id)
    state = loads(row["stage_state_json"], {}) or {}
    report = loads(row["report_json"])
    rule("Result")
    say(f"Company:  {row['resolved_name'] or row['company_name'] or row['domain']} ({row['resolved_domain'] or '-'})")
    say(f"Status:   {status.replace('_', ' ')}")
    b = state.get("budget", {})
    say(f"Spend:    {b.get('calls', 0)} Crustdata calls, about {b.get('credits', 0)} credits")
    u = state.get("llm_usage") or {}
    if u.get("calls"):
        via = "your Claude account" if u.get("backend") == "claude_cli" else "the Anthropic API"
        cost = f", ~${u['cost_usd']:.2f} API-equivalent" if u.get("cost_usd") else ""
        say(f"Claude:   {u['calls']} calls via {via}{cost}")
    if not report:
        say("No report was produced. Check the warnings above.")
        return None
    cov = (report.get("coverage") or {}).get("evidence_coverage")
    if cov is not None:
        say(f"Evidence: {round(cov * 100)}% of factual claims have a cited source")
    hyps = report.get("hypotheses") or []
    if hyps:
        say("\nHypotheses (interpretations, not facts):")
        for h in hyps:
            say(f"  - [{h.get('evidence_strength')}] {h['title']}")
            say(f"      {h['explanation']}")
    elif state.get("llm_down") or any("LLM" in w for w in state.get("warnings", [])):
        say("\nNo hypotheses: Claude wasn't available, so this is a facts-only report.")
    else:
        say("\nNo hypotheses: no signal cluster reached the evidence bar (an honest 'insufficient evidence' result).")
    if state.get("warnings"):
        say(f"\n{len(state['warnings'])} warning(s); they're listed in the brief under Unknowns.")
    EXPORTS.mkdir(exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "_", row["resolved_name"] or row["company_name"] or row["domain"] or inv_id).strip("_")
    path = EXPORTS / f"coredata_{name}_{datetime.now():%Y%m%d_%H%M}.html"
    path.write_text(render_html(report), encoding="utf-8")
    say(f"\nBrief saved to: {path}")
    if open_browser:
        webbrowser.open(path.resolve().as_uri())
        say("Opened it in your browser.")
    return path


# ------------------------------------------------------------------ main
def investigate(settings: Settings, input_fn: Callable[[str], str] = input, open_browser: bool = True, **runner_kw) -> str:
    db = DB(settings.db_path)
    t = collect_target(input_fn)
    inv_id = db.create_investigation(t["company_name"], t["domain"], t["question"], t["competitors"], settings.provider_mode)
    rule("Investigating")
    say(f"Investigation {inv_id}. This usually takes 1-3 minutes; web searches are the slowest part.\n")
    status = run_live(db, inv_id, settings, **runner_kw)
    while status == "needs_confirmation":
        confirm_company(db, inv_id, input_fn)
        rule("Investigating")
        status = run_live(db, inv_id, settings, **runner_kw)
    summarise(db, inv_id, status, open_browser)
    return inv_id


def main() -> None:
    say("=" * 64)
    say("  CoreData - competitor intelligence from public signals")
    say("=" * 64)
    settings = collect_keys(get_settings())
    while True:
        try:
            investigate(settings)
        except KeyboardInterrupt:
            say("\nStopped.")
        if not yes("\nInvestigate another company?"):
            break
    say("Done. You can close this window.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nBye.")
