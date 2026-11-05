"""Shared fixtures: temp DB, a fake Crustdata server (httpx.MockTransport) and a scripted LLM."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import DB  # noqa: E402
from app.llm.client import LLM, LLMError, LLMValidationError  # noqa: E402
from app.llm.schemas import (  # noqa: E402
    Alternative, FalsifyPlan, HypothesisOut, HypothesisResult, MatrixCell, ScopingResult, TestPick, Verdict, VerifyResult,
)
from app.models import Theme  # noqa: E402

TODAY = date.today()


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    return replace(s, crustdata_api_key="test-key", db_path=str(tmp_path / "t.db"), provider_mode="live", record=False,
                   fixtures_dir=tmp_path / "fixtures", credit_budget=500, max_api_calls=200, max_probes=25,
                   enable_people=True, enable_web=True, fail_endpoints=[], fail_llm=False, llm_fallbacks=False)


@pytest.fixture
def db(settings) -> DB:
    return DB(settings.db_path)


# ------------------------------------------------------------------ fake Crustdata
def _walk(f):
    if not f:
        return
    if "conditions" in f:
        for c in f["conditions"]:
            yield from _walk(c)
    else:
        yield f


def _window(conds) -> str:
    start = next((c["value"] for c in conds if c["field"] == "metadata.date_added" and c["type"] == "=>"), None)
    if start is None:
        return "all"
    d = date.fromisoformat(start)
    if d >= TODAY - timedelta(days=31):
        return "recent"
    if d >= TODAY - timedelta(days=121):
        return "prior"
    return "older"


COUNTS = {  # company -> theme? -> window -> count
    100: {False: {"recent": 200, "prior": 400, "older": 900}, True: {"recent": 12, "prior": 6, "older": 10}},
    200: {False: {"recent": 300, "prior": 600, "older": 900}, True: {"recent": 3, "prior": 3, "older": 3}},
}


def _job_rows():
    rows = []
    titles = ["Staff Robotics Engineer", "Robotics Software Engineer", "Perception Engineer", "Robotics Engineer",
              "Senior Robotics Engineer", "Head of Robotics"]
    for i, t in enumerate(titles):
        rows.append({
            "crustdata_job_id": 1000 + i,
            "job_details": {"title": t, "category": "Engineering", "url": f"https://jobs.example/{i}",
                            "reposted_job": i == 3, "source": "greenhouse" if i % 2 else "lever"},
            "location": {"raw": "Berlin, Germany" if i == 0 else "Austin, United States",
                         "city": "Berlin" if i == 0 else "Austin", "country": "Germany" if i == 0 else "United States"},
            "metadata": {"date_added": (TODAY - timedelta(days=3 + i)).isoformat()},
            "content": {"description": f"Build robot manipulation and motion planning systems ({i})"},
        })
    return rows


def crust_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    body = json.loads(request.content or b"{}")
    if path == "/company/identify":
        if "domains" in body:
            d = body["domains"][0]
            cid = {"acme.com": 100, "peer.com": 200}.get(d)
            matches = [] if cid is None else [{"confidence_score": 1.0, "company_data": {
                "crustdata_company_id": cid, "basic_info": {"name": d.split(".")[0].title(), "primary_domain": d}}}]
            return httpx.Response(200, json=[{"matched_on": d, "match_type": "domain", "matches": matches}])
        return httpx.Response(200, json={"results": [{"matched_on": body["names"][0], "matches": [
            {"confidence_score": 0.5, "company_data": {"crustdata_company_id": 100, "basic_info": {"name": "Acme", "primary_domain": "acme.com"}}},
            {"confidence_score": 0.49, "company_data": {"crustdata_company_id": 101, "basic_info": {"name": "Acme Ltd", "primary_domain": "acme.co.uk"}}},
        ]}]})
    if path == "/company/enrich":
        return httpx.Response(200, json=[{"matches": [{"company_data": {
            "basic_info": {"name": "Acme", "primary_domain": "acme.com", "industries": ["Industrial Automation"]},
            "headcount": {"total": 1200}, "funding": {"last_fundraise_date": "2025-06-01"}}}]}])
    if path == "/job/search":
        conds = list(_walk(body.get("filters")))
        cid = next((c["value"] for c in conds if c["field"] == "company.basic_info.company_id"), 100)
        theme = any(c["type"] in ("[.]", "(.)") for c in conds)
        w = _window(conds)
        aggs = body.get("aggregations") or []
        if body.get("limit") == 0 and aggs and aggs[0]["type"] == "count":
            n = COUNTS.get(cid, COUNTS[200])[theme].get(w, 0)
            return httpx.Response(200, json={"job_listings": [], "next_cursor": None, "total_count": n,
                                             "aggregations": [{"type": "count", "field": None, "value": n}]})
        if body.get("limit") == 0 and aggs:
            field = aggs[0]["field"]
            buckets = {
                ("job_details.title", "recent"): [("Robotics Engineer", 8), ("Perception Engineer", 4), ("Software Engineer", 40)],
                ("job_details.title", "prior"): [("Software Engineer", 80), ("Robotics Engineer", 2)],
                ("location.country", "recent"): [("United States", 150), ("Germany", 10)] if not theme else [("United States", 10), ("Germany", 2)],
            }.get((field, w), [("United States", 100)] if field == "location.country" else [("Engineering", 100)])
            return httpx.Response(200, json={"job_listings": [], "next_cursor": None, "total_count": 0, "aggregations": [
                {"type": "group_by", "field": field, "buckets": [{"key": k, "count": v, "metadata": {}} for k, v in buckets]}]})
        rows = _job_rows()
        if body.get("cursor"):
            return httpx.Response(200, json={"job_listings": rows[4:], "next_cursor": None, "total_count": len(rows)})
        return httpx.Response(200, json={"job_listings": rows[:4], "next_cursor": "CUR1", "total_count": len(rows)})
    if path == "/person/search":
        return httpx.Response(200, json={"profiles": [{
            "crustdata_person_id": 7, "basic_profile": {"name": "Pat Doe", "current_title": "Director of Robotics"},
            "experience": {"employment_details": {"current": {"name": "Acme", "title": "Director of Robotics",
                                                              "start_date": (TODAY - timedelta(days=40)).isoformat()}}},
            "social_handles": {"professional_network_identifier": {"profile_url": "https://linkedin.example/pat"}}}],
            "next_cursor": None, "total_count": 1})
    if path == "/web/search/live":
        return httpx.Response(200, json={"success": True, "results": [
            {"source": "web", "title": "Acme Robotics careers", "url": "https://acme.com/careers/robotics", "snippet": "Join our robotics team", "position": 1},
            {"source": "news", "title": "Acme hires for robots", "url": "https://techcrunch.com/acme-robots", "snippet": "Acme posted roles", "position": 2}]})
    if path == "/web/enrich/live":
        return httpx.Response(200, json=[{"success": True, "url": body["urls"][0], "title": "Careers", "content": "<p>Robotics team</p>"}])
    return httpx.Response(404, text="unknown endpoint")


@pytest.fixture
def transport():
    return httpx.MockTransport(crust_handler)


# ------------------------------------------------------------------ scripted LLM
class ScriptedLLM(LLM):
    """Deterministic stand-in for Claude; exercises the same validators as the real client."""

    def __init__(self, settings, planner_fails=True, hypothesis_mode="good"):
        super().__init__(settings=settings, client=object())
        self.planner_fails = planner_fails
        self.hypothesis_mode = hypothesis_mode

    def structured(self, prompt_file, variables, model_cls, validator=None, max_repairs=2, effort=None, system=None):
        if self.settings.fail_llm:
            raise LLMError("down")
        out = getattr(self, "_" + prompt_file.split("_")[0])(variables)
        if validator:
            errs = validator(out)
            if errs:
                raise LLMValidationError(errs)
        return out

    def tool_turn(self, system, messages, tools, effort="low"):
        raise LLMError("planner scripted failure")

    def _b1(self, v):
        themes = [Theme(id="t1", label="robotics", rationale="Tests the user question.", source="user_question",
                        title_keywords=["robotics", "perception"], description_phrases=["motion planning", "robot manipulation"],
                        anti_keywords=["robotic process automation"], tech_keywords=["ROS"], role_families=["engineering"])]
        if v["emerging_terms"]:
            term = v["emerging_terms"][0]["term"]
            themes.append(Theme(id="t2", label=term, rationale="Data-driven term.", source="data_driven",
                                title_keywords=[term], description_phrases=[term + " systems"], anti_keywords=[],
                                tech_keywords=[], role_families=["engineering"]))
        return ScopingResult(themes=themes)

    def _b3(self, v):
        raise LLMError("cluster scripted failure")

    def _b4(self, v):
        ids = [l.split(" | ")[0] for l in v["cluster_signals"].splitlines() if " | derived_metric | " not in l]
        sup = ids[:4]
        if self.hypothesis_mode == "ungrounded":
            stmt = "Signals suggest 97 robotics postings."
        elif self.hypothesis_mode == "banned":
            stmt = "Signals suggest they will launch a robot."
        else:
            stmt = "Signals suggest a robotics capability build-out across engineering postings."
        cells = [MatrixCell(signal_id=i, hypothesis_ref=r, relation="consistent" if r == "H1" else "neutral")
                 for i in sup for r in ("H1", "H2", "H3")]
        return HypothesisResult(hypotheses=[HypothesisOut(
            hypothesis_type="capability_buildout", title="Possible robotics capability build-out", statement=stmt,
            supporting_signal_ids=sup, contradicting_signal_ids=[],
            alternatives=[Alternative(ref="H2", statement="Roles may support an existing automation product.",
                                      would_be_supported_by="product pages", consistent_signal_ids=sup[:1]),
                          Alternative(ref="H3", statement="Postings may be evergreen reposts.",
                                      would_be_supported_by="reposted flags", consistent_signal_ids=[])],
            evidence_gaps=["No public product announcement found."],
            recommended_next_indicators=["Robotics product pages", "Further senior robotics postings"],
            matrix=cells, diagnostic_note="Seniority signals are the most diagnostic.")])

    def _b5(self, v):
        return FalsifyPlan(tests=[TestPick(test_id="novelty_test", reason="x"), TestPick(test_id="bogus_test", reason="x")],
                           disconfirming_web_queries=[])

    def _b6(self, v):
        ids = re.findall(r"\[(clm_\d+)\]", v["claims_with_evidence"])
        return VerifyResult(verdicts=[Verdict(claim_id=i, verdict="supported", reason="ok") for i in ids])

    def _b7(self, v):
        raise LLMError("brief scripted failure")
