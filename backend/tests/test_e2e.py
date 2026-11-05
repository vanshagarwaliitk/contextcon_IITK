"""End-to-end pipeline runs against the fake Crustdata server + PRD resilience scenarios (PLAYBOOK A18)."""
from dataclasses import replace

from app.db import loads
from app.llm.client import LLMError
from app.pipeline.brief import render_html
from app.pipeline.context import Ctx
from app.pipeline.planner import Planner
from app.pipeline.runner import run_investigation
from app.providers.crustdata.client import Budget, CrustdataClient
from conftest import ScriptedLLM


def run(db, settings, transport, llm=None, domain="acme.com", name=None, budget=None, competitors=("peer.com",)):
    inv = db.create_investigation(name, domain, "Are they moving into robotics?", list(competitors))
    crust = CrustdataClient(db=db, settings=settings, transport=transport, budget=budget, sleep=lambda s: None)
    status = run_investigation(inv, db=db, settings=settings, crust=crust, llm=llm or ScriptedLLM(settings))
    row = db.get_investigation(inv)
    return inv, status, loads(row["report_json"]), loads(row["stage_state_json"], {})


def test_full_run_produces_hypotheses_and_report(db, settings, transport):
    inv, status, report, state = run(db, settings, transport)
    assert status in ("completed", "completed_with_warnings")
    assert all(v == "done" for v in state["stages"].values()), state["stages"]
    assert report["hypotheses"], "expected at least one hypothesis"
    h = report["hypotheses"][0]
    sig_ids = {s["id"] for s in report["signals"]}
    assert h["hypothesis_type"] == "capability_buildout"
    assert set(h["supporting_signal_ids"]) <= sig_ids
    assert h["evidence_strength"] in ("Low", "Medium", "High")
    assert {t["test_id"] for t in h["tests"]} >= {"novelty_test", "evergreen_test", "scale_test", "peer_test"}
    assert report["coverage"]["evidence_coverage"] is not None
    assert all(c["claim_type"] != "fact" or c["signal_ids"] or c["status"] == "unverified" for c in report["claims"])
    html = render_html(report)
    assert "HYPOTHESIS" in html and "Source register" in html
    # anti-keyword / data-driven theme / planner fallback evidence in events
    msgs = " ".join(e["message"] for e in db.events_after(inv))
    assert "deterministic default plan" in msgs


def test_ambiguous_name_stops_pipeline(db, settings, transport):
    inv, status, report, state = run(db, settings, transport, domain=None, name="Acme")
    assert status == "needs_confirmation" and report is None
    assert "baseline" not in state["stages"]


def test_job_api_down_still_reports(db, settings, transport):
    s = replace(settings, fail_endpoints=["/job/search"])
    inv, status, report, state = run(db, s, transport, llm=ScriptedLLM(s))
    assert status == "completed_with_warnings" and report is not None
    assert report["outcome"] == "insufficient_evidence"
    assert any(c["status"] == "unverified" for c in report["claims"])


def test_web_down_hypotheses_without_corroboration(db, settings, transport):
    s = replace(settings, fail_endpoints=["/web/search/live"])
    inv, status, report, state = run(db, s, transport, llm=ScriptedLLM(s))
    assert report["hypotheses"]
    for h in report["hypotheses"]:
        corr = [t for t in h["tests"] if t["test_id"] == "corroboration_test"]
        assert corr and corr[0]["outcome"] == "neutral"


def test_llm_down_templated_report(db, settings, transport):
    s = replace(settings, fail_llm=True)
    inv, status, report, state = run(db, s, transport, llm=ScriptedLLM(s))
    assert report is not None and report["hypotheses"] == []
    assert report["sections"]["writer"] == "templated"
    assert "unavailable" in report["sections"]["executive_summary"][0]["text"]
    assert report["sections"]["key_findings"]


def test_budget_exceeded_partial_report(db, settings, transport):
    inv, status, report, state = run(db, settings, transport, budget=Budget(max_calls=8, max_credits=500))
    assert report is not None and status == "completed_with_warnings"


def test_both_people_and_web_off(db, settings, transport):
    s = replace(settings, enable_people=False, enable_web=False)
    inv, status, report, state = run(db, s, transport, llm=ScriptedLLM(s))
    assert report is not None
    assert not any(x["signal_type"] in ("web_evidence", "leadership_change") for x in report["signals"])


def test_planner_guards(db, settings, transport):
    inv = db.create_investigation(None, "acme.com", "q", [])
    crust = CrustdataClient(db=db, settings=settings, transport=transport)
    run_investigation(inv, db=db, settings=settings, crust=crust, llm=ScriptedLLM(settings))
    ctx = Ctx.load(db, inv, crust, ScriptedLLM(settings), settings)
    p = Planner(ctx)
    ok, payload = p.execute("job_theme_count", {"theme_id": "t1", "window": "recent", "reason": "again"})
    assert not ok and "duplicate" in payload["error"]
    ok, payload = p.execute("job_theme_count", {"theme_id": "t9", "window": "recent", "reason": "x"})
    assert not ok and "unknown theme" in payload["error"]
    crust.budget.calls = crust.budget.max_calls
    ok, payload = p.execute("job_tech", {"theme_id": "t1", "reason": "x"})  # not run by the default plan
    assert not ok and "budget" in payload["error"]


def test_rows_before_count_rejected(db, settings, transport):
    from app.models import save_themes
    from conftest import ScriptedLLM as S
    inv = db.create_investigation(None, "acme.com", "q", [])
    db.update_investigation(inv, resolved_company_id=100)
    crust = CrustdataClient(db=db, settings=settings, transport=transport)
    ctx = Ctx.load(db, inv, crust, S(settings), settings)
    save_themes(db, inv, S(settings)._b1({"emerging_terms": []}).themes)
    ok, payload = Planner(ctx).execute("job_theme_rows", {"theme_id": "t1", "reason": "x"})
    assert not ok and "before" in payload["error"]


def test_ungrounded_hypothesis_falls_back(db, settings, transport):
    inv, status, report, state = run(db, settings, transport, llm=ScriptedLLM(settings, hypothesis_mode="ungrounded"))
    assert report["hypotheses"] and all(h["hypothesis_type"] == "insufficient_evidence" for h in report["hypotheses"])
