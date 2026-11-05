import httpx
import pytest
import respx

from app.providers.crustdata.client import (
    Budget, BudgetExceeded, CrustdataAuthError, CrustdataBadRequest, CrustdataClient, CrustdataServerError,
    CrustdataUnavailable, ReplayMiss,
)
from dataclasses import replace

URL = "https://api.crustdata.com/job/search"


def client(db, settings, **kw):
    return CrustdataClient(db=db, settings=settings, sleep=lambda s: None, **kw)


@respx.mock
def test_headers_present(db, settings):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"total_count": 1}))
    client(db, settings).post("/job/search", {"limit": 0}, "inv", "t")
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.headers["x-api-version"] == "2025-11-01"


@respx.mock
def test_crustdata_api_key_is_sent_from_settings(db, settings):
    expected_key = "custom-test-key"
    settings = replace(settings, crustdata_api_key=expected_key)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"total_count": 1}))

    client(db, settings).post("/job/search", {"limit": 0})

    assert route.calls[0].request.headers["authorization"] == f"Bearer {expected_key}"


def test_missing_api_key_fails_before_request(db, settings):
    c = client(db, replace(settings, crustdata_api_key=""))
    with pytest.raises(CrustdataAuthError, match="CRUSTDATA_API_KEY is not set"):
        c.post("/job/search", {"limit": 0})


@respx.mock
def test_retry_on_429_then_success(db, settings):
    route = respx.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    assert client(db, settings).post("/job/search", {"a": 1}) == {"ok": 1}
    assert route.call_count == 2


@respx.mock
def test_no_retry_on_unsupported_500(db, settings):
    route = respx.post(URL).mock(return_value=httpx.Response(500, text="Unsupported filter type"))
    with pytest.raises(CrustdataServerError):
        client(db, settings).post("/job/search", {"a": 1})
    assert route.call_count == 1


@respx.mock
def test_no_retry_on_400(db, settings):
    route = respx.post(URL).mock(return_value=httpx.Response(400, text="bad"))
    with pytest.raises(CrustdataBadRequest):
        client(db, settings).post("/job/search", {"a": 1})
    assert route.call_count == 1


@respx.mock
def test_cache_hit(db, settings):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"x": 1}))
    c = client(db, settings)
    c.post("/job/search", {"b": 2, "a": 1})
    c.post("/job/search", {"a": 1, "b": 2})  # same canonical body
    assert route.call_count == 1


def test_replay_miss(db, settings):
    c = client(db, replace(settings, provider_mode="replay"))
    with pytest.raises(ReplayMiss):
        c.post("/job/search", {"never": "recorded"})


@respx.mock
def test_record_then_replay(db, settings):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"rec": 1}))
    client(db, replace(settings, record=True)).post("/job/search", {"q": 1})
    assert client(db, replace(settings, provider_mode="replay")).post("/job/search", {"q": 1}) == {"rec": 1}


def test_budget_exceeded(db, settings):
    c = client(db, settings, budget=Budget(max_calls=0, max_credits=10))
    with pytest.raises(BudgetExceeded):
        c.post("/job/search", {"a": 1})


def test_budget_charge_rows():
    b = Budget(10, 100)
    assert b.charge("/job/search", 5) == 5
    assert b.charge("/person/search", 10) == pytest.approx(0.3)
    assert b.charge("/job/search", 0, aggregation_only=True) == 0 and b.unverified_cost_calls == 1


def test_injected_failure(db, settings):
    with pytest.raises(CrustdataUnavailable):
        client(db, replace(settings, fail_endpoints=["/job/search"])).post("/job/search", {})


@respx.mock
def test_api_key_never_logged(db, settings):
    respx.post(URL).mock(return_value=httpx.Response(401, text="no"))
    with pytest.raises(Exception):
        client(db, settings).post("/job/search", {}, "inv")
    logs = db.query("SELECT * FROM api_logs")
    assert logs and all("test-key" not in str(r) for r in logs)
