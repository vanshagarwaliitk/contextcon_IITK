"""CLI end-to-end run (PLAYBOOK A16).

    python -m scripts.run_investigation --domain example.com --question "Are they moving into robotics?" \
        --competitors peer1.com,peer2.com [--html out.html]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import _path  # noqa: F401

from app.db import DB, loads
from app.config import get_settings
from app.pipeline.brief import render_html
from app.pipeline.runner import run_investigation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--name")
    ap.add_argument("--question")
    ap.add_argument("--competitors", default="")
    ap.add_argument("--resume", help="existing investigation id")
    ap.add_argument("--html", help="write the brief HTML here")
    a = ap.parse_args()
    s = get_settings()
    db = DB(s.db_path)
    inv = a.resume or db.create_investigation(a.name, a.domain, a.question,
                                              [c.strip() for c in a.competitors.split(",") if c.strip()], s.provider_mode)
    print(f"investigation {inv} (mode={s.provider_mode}, model={s.llm_model})")
    status = run_investigation(inv, db=db, settings=s)
    for e in db.events_after(inv):
        if e["stage"] in ("runner", "planner", "score") or e["level"] != "info" or "done" in e["message"]:
            print(f"  [{e['level']:5}] {e['stage']:<12} {e['message'][:160]}")
    row = db.get_investigation(inv)
    state = loads(row["stage_state_json"], {})
    print(f"\nstatus: {status}\nstages: {state.get('stages')}\nbudget: {state.get('budget')}\n"
          f"llm usage: {state.get('llm_usage')}\ncoverage: {state.get('coverage')}")
    if status == "needs_confirmation":
        print("candidates:", json.dumps(loads(row["candidates_json"], []), indent=1, default=str)[:2000])
        return
    report = loads(row["report_json"])
    if report:
        for h in report["hypotheses"]:
            print(f"\n- {h['id']} [{h['evidence_strength']}/{h['evidence_status']}] {h['title']}\n  {h['explanation']}"
                  f"\n  supporting={h['supporting_signal_ids']} contradicting={h['contradicting_signal_ids']}")
        if a.html:
            Path(a.html).write_text(render_html(report), encoding="utf-8")
            print(f"\nbrief written to {a.html}")


if __name__ == "__main__":
    main()
