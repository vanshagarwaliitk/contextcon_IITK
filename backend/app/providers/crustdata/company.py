"""Company Identify + Enrich adapters (POST /company/identify, /company/enrich)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from .client import CrustdataClient


class Candidate(BaseModel):
    crustdata_company_id: int
    name: Optional[str] = None
    primary_domain: Optional[str] = None
    confidence_score: Optional[float] = None
    basic_info: dict[str, Any] = {}


def _entries(data: Any) -> list[dict]:
    """Accept both a top-level array and a {"results": [...]} envelope."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        if "matches" in data:
            return [data]
    return []


def parse_candidates(data: Any) -> list[Candidate]:
    out: list[Candidate] = []
    for entry in _entries(data):
        for m in entry.get("matches") or []:
            cd = m.get("company_data") or {}
            bi = cd.get("basic_info") or {}
            cid = cd.get("crustdata_company_id") or bi.get("crustdata_company_id")
            if cid is None:
                continue
            out.append(
                Candidate(
                    crustdata_company_id=int(cid),
                    name=bi.get("name"),
                    primary_domain=(bi.get("primary_domain") or "").lower() or None,
                    confidence_score=m.get("confidence_score"),
                    basic_info=bi,
                )
            )
    return out


def normalise_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    d = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    if d.startswith("www."):
        d = d[4:]
    return d.split("/")[0] or None


def identify(
    client: CrustdataClient, investigation_id: str, name: str | None = None, domain: str | None = None
) -> list[Candidate]:
    domain = normalise_domain(domain)
    body = {"domains": [domain]} if domain else {"names": [name]}
    data = client.post("/company/identify", body, investigation_id, label="identify")
    return parse_candidates(data)


DEFAULT_ENRICH_FIELDS = ["basic_info", "headcount", "funding", "locations", "taxonomy", "competitors"]


def enrich(
    client: CrustdataClient, investigation_id: str, company_id: int, fields: list[str] | None = None
) -> dict[str, Any] | None:
    body = {"crustdata_company_ids": [company_id], "fields": fields or DEFAULT_ENRICH_FIELDS}
    data = client.post("/company/enrich", body, investigation_id, label="enrich")
    for entry in _entries(data):
        for m in entry.get("matches") or []:
            if m.get("company_data"):
                return m["company_data"]
        if entry.get("company_data"):
            return entry["company_data"]
    return None
