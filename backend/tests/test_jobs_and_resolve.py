import json
from datetime import date

from app.pipeline.resolve import decide
from app.providers.crustdata import company as co
from app.providers.crustdata import jobs


def test_query_builder_valid_syntax():
    w = jobs.windows(date(2026, 6, 30))["recent"]
    qb = jobs.JobQueryBuilder().company(5).window(w).theme_match(["robotics"], ["motion planning"])
    for body in (qb.count_body(), qb.group_by_body("location.country"), qb.rows_body(10)):
        s = json.dumps(body)
        assert '"column"' not in s and ">=" not in s and "<=" not in s
    f = qb.filters()
    assert f["op"] == "and"
    ors = f["conditions"][-1]
    assert ors["op"] == "or" and {c["type"] for c in ors["conditions"]} == {"[.]", "(.)"}
    assert any(c["type"] == "=>" for c in f["conditions"])


def test_windows_are_contiguous():
    w = jobs.windows(date(2026, 6, 30))
    assert w["older"].end == w["prior"].start and w["prior"].end == w["recent"].start


def test_pagination_stops_on_null_cursor(db, settings, transport):
    from app.providers.crustdata.client import CrustdataClient
    c = CrustdataClient(db=db, settings=settings, transport=transport)
    rows, _ = jobs.rows(c, "inv", jobs.JobQueryBuilder().company(100), "t", limit=25, max_pages=5)
    assert len(rows) == 6  # 4 + 2, second page has next_cursor null


def test_dedupe_and_hash():
    r = {"crustdata_job_id": 1, "job_details": {"title": "A  B"}, "location": {"city": "X"}, "content": {"description": "d"}}
    out = jobs.dedupe_rows([r, dict(r)])
    assert len(out) == 1 and out[0]["_dup_key"] == "a b|x" and out[0]["_desc_hash"]


def test_anti_keywords():
    rows = [{"job_details": {"title": "Robotic Process Automation Developer"}}, {"job_details": {"title": "Robotics Engineer"}}]
    assert len(jobs.apply_anti_keywords(rows, ["robotic process automation"])) == 1


def test_identify_both_envelopes():
    m = {"matches": [{"confidence_score": 1, "company_data": {"crustdata_company_id": 1, "basic_info": {"primary_domain": "A.com"}}}]}
    assert co.parse_candidates([m])[0].primary_domain == "a.com"
    assert co.parse_candidates({"results": [m]})[0].crustdata_company_id == 1


def test_resolution_policy():
    a = co.Candidate(crustdata_company_id=1, name="A", primary_domain="a.com", confidence_score=0.5)
    b = co.Candidate(crustdata_company_id=2, name="A2", primary_domain="a2.com", confidence_score=0.49)
    assert decide([a, b], "https://www.a.com/")[0] == "matched"
    assert decide([a, b], None)[0] == "ambiguous"
    assert decide([a], None)[0] == "needs_confirmation"
    assert decide([], "x.com")[0] == "not_found"
