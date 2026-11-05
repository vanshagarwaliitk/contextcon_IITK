"""Hour-1 API discovery (PLAYBOOK A3): empirically confirm Crustdata shapes/costs for YOUR account.

    python -m scripts.discover example.com --theme "robotics" [--record]

Writes docs/crustdata_findings.md. With --record, every response is saved under fixtures/ for replay mode.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from . import _path  # noqa: F401

from app.config import ROOT, get_settings
from app.db import DB
from app.providers.crustdata import jobs
from app.providers.crustdata.client import CrustdataClient, CrustdataError

FINDINGS = ROOT / "docs" / "crustdata_findings.md"


def shape(data, depth=0):
    if depth > 2:
        return "…"
    if isinstance(data, dict):
        return {k: shape(v, depth + 1) for k, v in list(data.items())[:15]}
    if isinstance(data, list):
        return [shape(data[0], depth + 1), f"... {len(data)} items"] if data else []
    return type(data).__name__


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--theme", default="robotics")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    if a.record:
        os.environ["RECORD"] = "1"
    os.environ["PROVIDER_MODE"] = "live"
    s = get_settings()
    if not s.crustdata_api_key:
        raise SystemExit("Set CRUSTDATA_API_KEY in .env first")
    db = DB(str(ROOT / "discover.db"))
    inv = db.create_investigation(None, a.domain, f"discover {a.theme}")
    c = CrustdataClient(db=db, settings=s)
    log: list[dict] = []

    def call(label, endpoint, body):
        t = time.monotonic()
        try:
            data = c.post(endpoint, body, inv, label)
            res = {"label": label, "endpoint": endpoint, "status": 200, "ms": int((time.monotonic() - t) * 1000),
                   "shape": shape(data), "sample": json.dumps(data, default=str)[:1200]}
        except CrustdataError as exc:
            data = None
            res = {"label": label, "endpoint": endpoint, "status": exc.status_code, "error": str(exc)[:400]}
        res["request"] = body
        log.append(res)
        print(f"\n=== {label} {endpoint} -> {res.get('status')}\n{json.dumps(res.get('shape') or res.get('error'), indent=1)[:1500]}")
        return data

    today = date.today()
    d30 = (today - timedelta(days=30)).isoformat()
    ident = call("1 identify", "/company/identify", {"domains": [a.domain]})
    entries = ident if isinstance(ident, list) else (ident or {}).get("results", [])
    cid = None
    try:
        cid = entries[0]["matches"][0]["company_data"]["crustdata_company_id"]
    except (IndexError, KeyError, TypeError):
        pass
    print(f"\ncrustdata_company_id = {cid}")
    if cid is None:
        raise SystemExit("identify returned no match; check the domain")
    call("2 enrich", "/company/enrich", {"crustdata_company_ids": [cid], "fields": ["basic_info", "headcount", "funding"]})
    base = jobs.JobQueryBuilder().company(cid)
    base._conds.append(jobs.cond("metadata.date_added", "=>", d30))
    call("3 job count 30d", "/job/search", base.count_body())
    for f in ["job_details.category", "job_details.title", "location.country", "job_details.workplace_type", "job_details.source"]:
        call(f"4 group_by {f}", "/job/search", base.group_by_body(f, 30))
    theme = jobs.JobQueryBuilder().company(cid)
    theme._conds.append(jobs.cond("metadata.date_added", "=>", d30))
    theme.theme_match([a.theme], [a.theme])
    body = theme.rows_body(5)
    call("5 theme rows", "/job/search", body)
    call("6 autocomplete", "/job/search/autocomplete", {"field": "job_details.title", "query": a.theme, "limit": 10})
    call("7 person search", "/person/search", {
        "filters": {"op": "and", "conditions": [
            {"field": "experience.employment_details.current.company_id", "type": "=", "value": cid},
            {"field": "experience.employment_details.current.title", "type": "(.)", "value": "Director"}]},
        "fields": ["basic_profile.name", "experience.employment_details.current.title",
                   "experience.employment_details.current.start_date"], "limit": 3})
    web = call("8 web search", "/web/search/live", {"query": f"{a.domain} {a.theme}", "sources": ["web", "news"]})
    url = next((r.get("url") for r in (web or {}).get("results", []) if isinstance(r, dict) and r.get("url")), None)
    if url:
        call("8b web fetch", "/web/enrich/live", {"urls": [url]})

    FINDINGS.parent.mkdir(exist_ok=True)
    lines = [f"# Crustdata findings ({today}, domain {a.domain}, theme '{a.theme}')", "",
             f"API version 2025-11-01. Budget used: {c.budget.calls} calls, ~{c.budget.credits:.1f} est. credits "
             "(credit cost of limit:0 aggregations is UNVERIFIED — check the dashboard before/after).", "",
             "| step | endpoint | status | latency ms |", "|---|---|---|---|"]
    lines += [f"| {r['label']} | {r['endpoint']} | {r.get('status')} | {r.get('ms', '')} |" for r in log]
    for r in log:
        lines += ["", f"## {r['label']} — `{r['endpoint']}`", "Request:", "```json", json.dumps(r["request"], indent=1), "```"]
        if "error" in r:
            lines += [f"**Error:** {r['error']}"]
        else:
            lines += ["Response shape:", "```json", json.dumps(r["shape"], indent=1)[:3000], "```"]
    lines += ["", "## Notes to fill in by hand", "- Endpoints NOT permitted on this account:",
              "- Observed credits per call type (dashboard delta):", "- Differences vs. docs:"]
    FINDINGS.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {FINDINGS}")


if __name__ == "__main__":
    main()
