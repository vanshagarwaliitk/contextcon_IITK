"""Job Search adapter (POST /job/search) with a typed query builder.

Syntax per https://docs.crustdata.com/job-docs/search/reference.md:
  condition = {"field", "type", "value"}; group = {"op": "and"|"or", "conditions": [...]}
  operators: = != < =< > => in not_in is_null is_not_null (.) [.]
  NOTE: "field" (not "column"), "=>"/"=<" (not ">="/"<=").
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from .client import CrustdataClient

ENDPOINT = "/job/search"
ROW_FIELDS = [
    "crustdata_job_id",
    "job_details.title",
    "job_details.category",
    "job_details.url",
    "job_details.reposted_job",
    "job_details.source",
    "job_details.number_of_openings",
    "job_details.workplace_type",
    "location.raw",
    "location.city",
    "location.country",
    "metadata.date_added",
    "metadata.date_updated",
    "company.basic_info.name",
    "content.description",
]


# ------------------------------------------------------------------ windows
@dataclass(frozen=True)
class Window:
    name: str
    start: date  # inclusive
    end: date    # exclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def windows(today: date | None = None) -> dict[str, Window]:
    t = today or date.today()
    end = t + timedelta(days=1)
    return {
        "recent": Window("recent", t - timedelta(days=30), end),
        "prior": Window("prior", t - timedelta(days=120), t - timedelta(days=30)),
        "older": Window("older", t - timedelta(days=365), t - timedelta(days=120)),
    }


# ------------------------------------------------------------------ query builder
def cond(field: str, op: str, value: Any) -> dict:
    assert op not in (">=", "<="), "Crustdata uses => and =<"
    return {"field": field, "type": op, "value": value}


def group(op: str, conditions: Iterable[dict]) -> dict:
    conds = [c for c in conditions if c]
    if len(conds) == 1:
        return conds[0]
    return {"op": op, "conditions": conds}


class JobQueryBuilder:
    def __init__(self) -> None:
        self._conds: list[dict] = []

    def company(self, company_id: int) -> "JobQueryBuilder":
        self._conds.append(cond("company.basic_info.company_id", "=", int(company_id)))
        return self

    def window(self, w: Window) -> "JobQueryBuilder":
        self._conds.append(cond("metadata.date_added", "=>", w.start.isoformat()))
        self._conds.append(cond("metadata.date_added", "<", w.end.isoformat()))
        return self

    def theme_match(self, title_keywords: list[str], description_phrases: list[str]) -> "JobQueryBuilder":
        ors = [cond("content.description", "[.]", p) for p in description_phrases if p.strip()]
        ors += [cond("job_details.title", "(.)", k) for k in title_keywords if k.strip()]
        if ors:
            self._conds.append(group("or", ors))
        return self

    def category(self, cat: str) -> "JobQueryBuilder":
        self._conds.append(cond("job_details.category", "=", cat))
        return self

    def country(self, c: str) -> "JobQueryBuilder":
        self._conds.append(cond("location.country", "=", c))
        return self

    def technologies(self, names: list[str]) -> "JobQueryBuilder":
        # filter-only; case-sensitive; matches professional_network-sourced postings only
        self._conds.append(cond("technographics.technologies.name", "in", list(names)))
        return self

    def reposted(self, flag: bool) -> "JobQueryBuilder":
        self._conds.append(cond("job_details.reposted_job", "=", flag))
        return self

    def filters(self) -> dict:
        return group("and", self._conds)

    def count_body(self) -> dict:
        return {"filters": self.filters(), "limit": 0, "aggregations": [{"type": "count"}]}

    def group_by_body(self, field: str, size: int = 50) -> dict:
        return {
            "filters": self.filters(),
            "limit": 0,
            "aggregations": [{"type": "group_by", "field": field, "agg": "count", "size": size}],
        }

    def rows_body(self, limit: int, cursor: str | None = None) -> dict:
        body = {
            "filters": self.filters(),
            "fields": ROW_FIELDS,
            "limit": limit,
            "sorts": [{"field": "metadata.date_added", "order": "desc"}],
        }
        if cursor:
            body["cursor"] = cursor
        return body


# ------------------------------------------------------------------ response parsing
def parse_count(data: dict) -> int:
    for agg in data.get("aggregations") or []:
        if agg.get("type") == "count" and agg.get("value") is not None:
            return int(agg["value"])
    return int(data.get("total_count") or 0)


def parse_buckets(data: dict) -> list[dict]:
    for agg in data.get("aggregations") or []:
        if agg.get("type") == "group_by":
            return [{"key": b.get("key"), "count": int(b.get("count") or 0)} for b in agg.get("buckets") or []]
    return []


def listings(data: dict) -> list[dict]:
    return data.get("job_listings") or data.get("jobs") or []


def get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


_ws = re.compile(r"\s+")


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Dedupe by crustdata_job_id and annotate dup_key / desc_hash."""
    seen: set[Any] = set()
    out: list[dict] = []
    for r in rows:
        jid = r.get("crustdata_job_id") or get_path(r, "job_details.url")
        if jid in seen:
            continue
        seen.add(jid)
        title = _ws.sub(" ", (get_path(r, "job_details.title") or "").lower()).strip()
        city = (get_path(r, "location.city") or "").lower().strip()
        desc = (get_path(r, "content.description") or "")[:300]
        r = dict(r)
        r["_dup_key"] = f"{title}|{city}"
        r["_norm_title"] = title
        r["_desc_hash"] = hashlib.sha1(desc.encode()).hexdigest() if desc else None
        out.append(r)
    return out


def apply_anti_keywords(rows: list[dict], anti_keywords: list[str]) -> list[dict]:
    """Client-side NOT filter (API cannot express not-match on text)."""
    if not anti_keywords:
        return rows
    anti = [a.lower() for a in anti_keywords if a.strip()]
    keep = []
    for r in rows:
        blob = " ".join(
            str(x or "") for x in (get_path(r, "job_details.title"), get_path(r, "content.description"))
        ).lower()
        if not any(a in blob for a in anti):
            keep.append(r)
    return keep


# ------------------------------------------------------------------ calls
def count(client: CrustdataClient, inv: str, qb: JobQueryBuilder, label: str) -> tuple[int, dict]:
    body = qb.count_body()
    return parse_count(client.post(ENDPOINT, body, inv, label)), body


def group_by(client: CrustdataClient, inv: str, qb: JobQueryBuilder, field: str, label: str, size: int = 50):
    body = qb.group_by_body(field, size)
    return parse_buckets(client.post(ENDPOINT, body, inv, label)), body


def rows(
    client: CrustdataClient, inv: str, qb: JobQueryBuilder, label: str, limit: int = 25, max_pages: int = 2
) -> tuple[list[dict], dict]:
    out: list[dict] = []
    cursor = None
    first_body = None
    for _ in range(max_pages):
        body = qb.rows_body(min(limit - len(out), 50), cursor)  # identical filters across pages
        first_body = first_body or body
        data = client.post(ENDPOINT, body, inv, label)
        out.extend(listings(data))
        cursor = data.get("next_cursor")
        if not cursor or len(out) >= limit:
            break
    return dedupe_rows(out), first_body or {}


def autocomplete(client: CrustdataClient, inv: str, field: str, query: str) -> list[str]:
    body = {"field": field, "query": query, "limit": 10}
    data = client.post("/job/search/autocomplete", body, inv, "autocomplete")
    if isinstance(data, dict):
        vals = data.get("suggestions") or data.get("results") or []
    else:
        vals = data or []
    return [v.get("value", v) if isinstance(v, dict) else v for v in vals]
