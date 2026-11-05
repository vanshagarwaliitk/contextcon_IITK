"""test_factors.md factors are applied inside Crustdata queries and flow through to features/tests."""
import json

from app.pipeline import factors, falsify
from app.providers.crustdata.jobs import JobQueryBuilder
from conftest import _walk
from test_e2e import run


def _conds(body):
    return list(_walk(body["filters"]))


def test_every_factor_constrains_the_query_with_valid_syntax():
    for f in factors.JOB_FACTORS:
        body = f.apply(JobQueryBuilder().company(1)).count_body()
        raw = json.dumps(body)
        assert '"column"' not in raw and '">="' not in raw and '"<="' not in raw
        conds = _conds(body)
        assert len(conds) > 1, f"{f.id} added no constraint"
        assert body["limit"] == 0  # factors never fetch rows


def test_compliance_factor_excludes_legal_roles():
    conds = _conds(factors.FACTOR_BY_ID["compliance"].apply(JobQueryBuilder()).count_body())
    assert any(c["field"] == "content.description" and c["type"] == "[.]" and c["value"] == "HIPAA" for c in conds)
    assert any(c["field"] == "job_details.category" and c["type"] == "not_in" for c in conds)


def test_ratio_and_share():
    assert factors.ratio(1, 3) == 0.25
    assert factors.ratio(0, 0) is None and factors.ratio(None, 2) is None
    assert factors.share(5, 20) == 0.25 and factors.share(5, 0) is None


def _h(t):
    return {"hypothesis_type": t, "supporting_signal_ids": [], "theme_id": "t1"}


def test_gtm_test():
    f = {"company_gtm_share_recent": 0.5, "company_gtm_share_prior": 0.3, "_ids": {}}
    assert falsify.gtm_test(_h("go_to_market_shift"), {}, f).outcome == "supports"
    f["company_gtm_share_recent"] = 0.3
    assert falsify.gtm_test(_h("go_to_market_shift"), {}, f).outcome == "contradicts"
    assert falsify.gtm_test(_h("capability_buildout"), {}, f).outcome == "neutral"
    assert falsify.gtm_test(_h("go_to_market_shift"), {}, {}).outcome == "inconclusive"


def test_compliance_zero_to_one_scale_phase():
    assert falsify.compliance_test(_h("new_customer_segment"), {}, {"theme_compliance_postings_recent": 2}).outcome == "supports"
    assert falsify.compliance_test(_h("new_customer_segment"), {}, {"theme_compliance_postings_recent": 0,
                                   "company_compliance_postings_recent": 1, "company_compliance_postings_prior": 1}).outcome == "neutral"
    assert falsify.zero_to_one_test(_h("capability_buildout"), {}, {"theme_zero_to_one_postings_recent": 1}).outcome == "supports"
    assert falsify.scale_phase_test(_h("contraction_or_cost_cutting"), {},
                                    {"company_junior_share_recent": 0.6, "company_zero_to_one_share_recent": 0.1}).outcome == "contradicts"


def test_select_tests_adds_factor_tests_by_type():
    assert "zero_to_one_test" in falsify.select_tests([], "capability_buildout")
    assert {"compliance_test", "gtm_test"} <= set(falsify.select_tests([], "new_customer_segment"))
    assert "gtm_test" not in falsify.select_tests([], "new_geography")


def test_full_run_applies_factors(db, settings, transport):
    inv, status, report, state = run(db, settings, transport)
    comp = state["factors"]
    for f in factors.JOB_FACTORS:
        for w in factors.COMPANY_WINDOWS:
            assert isinstance(comp[f"{w}:{f.id}"]["value"], int), f"{w}:{f.id} missing"
    # factor constraints really reached Crustdata
    sent = [json.loads(r["raw_ref"]) for r in db.query("SELECT raw_ref FROM signals WHERE investigation_id=? AND title LIKE 'Factor compliance%'", (inv,))]
    assert sent and all(any(c.get("value") == "HIPAA" for c in _walk(b["filters"])) for b in sent)
    feats = state["features"]
    tf = next(v for v in feats.values() if "theme_zero_to_one_postings_recent" in v)
    assert "company_gtm_share_recent" in tf and "company_junior_share_recent" in tf
    h = report["hypotheses"][0]
    assert "zero_to_one_test" in {t["test_id"] for t in h["tests"]}
    assert any(s["features"].get("web_factor") for s in report["signals"])


def test_factors_can_be_disabled(db, settings, transport):
    from dataclasses import replace
    s = replace(settings, enable_factors=False)
    inv, status, report, state = run(db, s, transport)
    assert not state.get("factors")
