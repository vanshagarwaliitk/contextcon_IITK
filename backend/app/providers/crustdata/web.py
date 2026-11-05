"""Crustdata Web Search (/web/search/live) + Web Fetch (/web/enrich/live)."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .client import CrustdataClient

SEARCH = "/web/search/live"
FETCH = "/web/enrich/live"

NEWS_HINTS = (
    "techcrunch", "reuters", "bloomberg", "theverge", "wired", "cnbc", "forbes", "venturebeat",
    "businesswire", "prnewswire", "economictimes", "livemint", "yourstory", "inc42", "ft.com", "wsj",
)


def classify_source(url: str | None, company_domain: str | None) -> str:
    """primary (company's own site) | secondary (news / wires) | other. Pure domain matching."""
    if not url:
        return "other"
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if company_domain:
        cd = company_domain.lower()
        if host == cd or host.endswith("." + cd):
            return "primary"
    if any(h in host for h in NEWS_HINTS) or host.startswith("news."):
        return "secondary"
    return "other"


def search(client: CrustdataClient, inv: str, query: str, label: str = "web_evidence") -> tuple[list[dict], dict]:
    body = {"query": query, "sources": ["web", "news"]}
    data = client.post(SEARCH, body, inv, label)
    results = (data or {}).get("results") or []
    out = []
    for r in results:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        out.append(
            {
                "title": r.get("title") or r.get("url"),
                "url": r.get("url"),
                "snippet": (r.get("snippet") or "")[:400],
                "date": r.get("date") or r.get("published_date"),
                "source": r.get("source"),
            }
        )
    return out, body


_tag = re.compile(r"<[^>]+>")


def fetch(client: CrustdataClient, inv: str, urls: list[str]) -> list[dict[str, Any]]:
    body = {"urls": urls[:2]}
    data = client.post(FETCH, body, inv, "web_fetch")
    out = []
    for entry in data if isinstance(data, list) else []:
        if not entry.get("success") or not entry.get("url"):
            continue
        text = _tag.sub(" ", entry.get("content") or "")
        out.append({"url": entry["url"], "title": entry.get("title"), "text": " ".join(text.split())[:1500]})
    return out
