"""Person Search adapter (POST /person/search). Public professional fields only."""
from __future__ import annotations

from typing import Any

from .client import CrustdataClient
from .jobs import cond, get_path, group

ENDPOINT = "/person/search"
# Returned paths (filter paths differ — see person-docs/search/reference).
FIELDS = [
    "crustdata_person_id",
    "basic_profile.name",
    "basic_profile.current_title",
    "experience.employment_details.current.name",
    "experience.employment_details.current.title",
    "experience.employment_details.current.start_date",
    "experience.employment_details.current.seniority_level",
    "social_handles.professional_network_identifier.profile_url",
]

SENIOR_TERMS = ["Head", "Director", "VP", "Vice President", "Chief", "Lead", "Principal"]


def search_leaders(
    client: CrustdataClient, inv: str, company_id: int, title_keywords: list[str], limit: int = 10
) -> tuple[list[dict], dict]:
    """Current employees whose title matches a senior term AND a theme keyword."""
    senior = group("or", [cond("experience.employment_details.current.title", "(.)", t) for t in SENIOR_TERMS])
    theme = group(
        "or", [cond("experience.employment_details.current.title", "(.)", k) for k in title_keywords[:6] if k]
    )
    body = {
        "filters": group("and", [cond("experience.employment_details.current.company_id", "=", int(company_id)), senior, theme]),
        "fields": FIELDS,
        "limit": min(limit, 10),
        "sorts": [{"field": "experience.employment_details.current.start_date", "order": "desc"}],
    }
    data = client.post(ENDPOINT, body, inv, "people_leadership")
    profiles = (data or {}).get("profiles") or []
    return [_minimal(p) for p in profiles], body


def _minimal(p: dict[str, Any]) -> dict[str, Any]:
    """Keep only name, title, employer, start date, public profile URL (no contact details)."""
    cur = get_path(p, "experience.employment_details.current")
    if isinstance(cur, list):
        cur = cur[0] if cur else {}
    cur = cur or {}
    return {
        "person_id": p.get("crustdata_person_id"),
        "name": get_path(p, "basic_profile.name"),
        "title": cur.get("title") or get_path(p, "basic_profile.current_title"),
        "employer": cur.get("name"),
        "start_date": cur.get("start_date"),
        "seniority_level": cur.get("seniority_level"),
        "profile_url": get_path(p, "social_handles.professional_network_identifier.profile_url"),
    }
