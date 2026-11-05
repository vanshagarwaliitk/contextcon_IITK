"""Crustdata HTTP client: budget, per-investigation cache, record/replay, retries, api_logs.

Every Crustdata call in the system goes through `CrustdataClient.post`.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ...config import Settings, get_settings
from ...db import DB, now_iso


# ------------------------------------------------------------------ errors
class CrustdataError(Exception):
    def __init__(self, message: str, status_code: int | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class CrustdataAuthError(CrustdataError): ...
class CrustdataBadRequest(CrustdataError): ...
class CrustdataPermission(CrustdataError): ...
class CrustdataNotFound(CrustdataError): ...
class CrustdataServerError(CrustdataError): ...
class CrustdataUnavailable(CrustdataError): ...  # network / injected failure
class ReplayMiss(CrustdataError): ...
class BudgetExceeded(CrustdataError): ...


# ------------------------------------------------------------------ budget
# Credits charged per row returned (or per call). Values from Crustdata pricing docs;
# job/search limit:0 aggregations are UNVERIFIED -> charged 0 and flagged.
CREDIT_TABLE: dict[str, dict[str, float]] = {
    "/job/search": {"per_row": 1.0, "per_call": 0.0},
    "/job/search/autocomplete": {"per_row": 0.0, "per_call": 0.0},
    "/person/search": {"per_row": 0.03, "per_call": 0.0},
    "/company/identify": {"per_row": 0.0, "per_call": 0.0},
    "/company/enrich": {"per_row": 0.0, "per_call": 1.0},
    "/web/search/live": {"per_row": 0.0, "per_call": 1.0},
    "/web/enrich/live": {"per_row": 0.0, "per_call": 1.0},
}


@dataclass
class Budget:
    max_calls: int
    max_credits: float
    calls: int = 0
    credits: float = 0.0
    unverified_cost_calls: int = 0

    @staticmethod
    def estimate(endpoint: str, rows: int) -> float:
        cfg = CREDIT_TABLE.get(endpoint, {"per_row": 1.0, "per_call": 1.0})
        return cfg["per_call"] + cfg["per_row"] * rows

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"call budget exhausted ({self.calls}/{self.max_calls})")
        if self.credits >= self.max_credits:
            raise BudgetExceeded(f"credit budget exhausted ({self.credits:.1f}/{self.max_credits})")

    def charge(self, endpoint: str, rows_returned: int, aggregation_only: bool = False) -> float:
        cost = self.estimate(endpoint, rows_returned)
        if aggregation_only and endpoint == "/job/search":
            self.unverified_cost_calls += 1
        self.calls += 1
        self.credits += cost
        return cost

    @property
    def calls_left(self) -> int:
        return max(0, self.max_calls - self.calls)

    @property
    def credits_left(self) -> float:
        return max(0.0, self.max_credits - self.credits)


def body_key(endpoint: str, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((endpoint + canonical).encode()).hexdigest()


def _rows_in(endpoint: str, data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    for key in ("job_listings", "jobs", "profiles", "results"):
        if isinstance(data.get(key), list):
            return len(data[key])
    return 0


# ------------------------------------------------------------------ client
@dataclass
class CrustdataClient:
    db: DB
    settings: Settings = field(default_factory=get_settings)
    budget: Budget | None = None
    transport: httpx.BaseTransport | None = None  # for tests
    sleep: Any = time.sleep
    _cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.budget is None:
            self.budget = Budget(self.settings.max_api_calls, self.settings.credit_budget)
        self._http = httpx.Client(
            base_url=self.settings.crustdata_base_url,
            timeout=30.0,
            transport=self.transport,
            headers={
                "authorization": f"Bearer {self.settings.crustdata_api_key}",
                "content-type": "application/json",
                "x-api-version": self.settings.crustdata_api_version,
            },
        )

    @property
    def mode(self) -> str:
        return self.settings.provider_mode

    # -------------------------------------------------------------- public
    def post(self, endpoint: str, body: dict, investigation_id: str | None = None, label: str = "") -> Any:
        key = body_key(endpoint, body)
        if key in self._cache:
            self._log(investigation_id, endpoint, label, 200, 0.0, None, 0, cache_hit=True)
            return self._cache[key]

        if any(endpoint.startswith(f) for f in self.settings.fail_endpoints):
            self._log(investigation_id, endpoint, label, None, 0.0, "injected failure (FAIL_ENDPOINT)", 0)
            raise CrustdataUnavailable(f"{endpoint} disabled by FAIL_ENDPOINT", endpoint=endpoint)

        if self.mode == "replay":
            data = self._replay(key, endpoint)
            self._log(investigation_id, endpoint, label, 200, 0.0, None, 0, cache_hit=True)
            self._cache[key] = data
            return data

        self.budget.check()
        start = time.monotonic()
        try:
            data = self._send_with_retry(endpoint, body)
        except CrustdataError as exc:
            latency = int((time.monotonic() - start) * 1000)
            self._log(investigation_id, endpoint, label, exc.status_code, 0.0, str(exc)[:300], latency)
            raise
        latency = int((time.monotonic() - start) * 1000)
        cost = self.budget.charge(endpoint, _rows_in(endpoint, data), aggregation_only=body.get("limit") == 0)
        self._log(investigation_id, endpoint, label, 200, cost, None, latency)
        self._cache[key] = data
        if self.settings.record:
            self._record(key, endpoint, body, data)
        return data

    # -------------------------------------------------------------- internals
    def _send_with_retry(self, endpoint: str, body: dict, max_retries: int = 2) -> Any:
        attempt = 0
        while True:
            try:
                resp = self._http.post(endpoint, json=body)
            except httpx.HTTPError as exc:
                if attempt < max_retries:
                    attempt += 1
                    self._backoff(attempt)
                    continue
                raise CrustdataUnavailable(f"network error: {type(exc).__name__}", endpoint=endpoint) from exc

            if resp.status_code < 400:
                return resp.json()

            text = resp.text[:500]
            code = resp.status_code
            retryable = code == 429 or (code >= 500 and "unsupported" not in text.lower())
            if retryable and attempt < max_retries:
                attempt += 1
                self._backoff(attempt)
                continue
            raise self._error_for(code, text, endpoint)

    def _backoff(self, attempt: int) -> None:
        self.sleep(min(8.0, (2 ** attempt) * 0.5) + random.uniform(0, 0.3))

    @staticmethod
    def _error_for(code: int, text: str, endpoint: str) -> CrustdataError:
        cls = {
            400: CrustdataBadRequest,
            401: CrustdataAuthError,
            403: CrustdataPermission,
            404: CrustdataNotFound,
        }.get(code, CrustdataServerError if code >= 500 else CrustdataError)
        return cls(f"HTTP {code} on {endpoint}: {text}", status_code=code, endpoint=endpoint)

    def _fixture_path(self, key: str) -> Path:
        return self.settings.fixtures_dir / f"{key}.json"

    def _replay(self, key: str, endpoint: str) -> Any:
        path = self._fixture_path(key)
        if not path.exists():
            raise ReplayMiss(f"no recorded fixture for {endpoint} ({key[:12]})", endpoint=endpoint)
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def _record(self, key: str, endpoint: str, body: dict, data: Any) -> None:
        self.settings.fixtures_dir.mkdir(parents=True, exist_ok=True)
        self._fixture_path(key).write_text(
            json.dumps({"endpoint": endpoint, "request": body, "recorded_at": now_iso(), "response": data}, indent=1),
            encoding="utf-8",
        )

    def _log(
        self,
        investigation_id: str | None,
        endpoint: str,
        label: str,
        status: int | None,
        credits: float,
        error: str | None,
        latency_ms: int,
        cache_hit: bool = False,
    ) -> None:
        # NOTE: never log headers / the API key.
        self.db.insert(
            "api_logs",
            {
                "investigation_id": investigation_id,
                "provider": "crustdata",
                "endpoint": endpoint,
                "label": label,
                "status_code": status,
                "credits_estimate": credits,
                "error": error,
                "latency_ms": latency_ms,
                "cache_hit": int(cache_hit),
                "created_at": now_iso(),
            },
        )
