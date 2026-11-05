"""Deterministic wording / grounding checks shared by hypothesis, verifier and brief."""
from __future__ import annotations

import json
import re
from typing import Any

BANNED_PHRASES = [
    "will launch", "is launching", "are launching", "definitely", "confirmed", "plans to", "planning to",
    "is building", "are building", "will enter", "is entering", "guaranteed", "certainly",
]
SAFE_STARTS = ("signals suggest", "may indicate", "worth investigating")

_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")


def banned_in(text: str) -> list[str]:
    t = text.lower()
    return [p for p in BANNED_PHRASES if p in t]


def starts_safe(text: str) -> bool:
    return text.strip().lower().startswith(SAFE_STARTS)


def _norm(n: str) -> str:
    n = n.rstrip("%")
    try:
        f = float(n)
    except ValueError:
        return n
    return str(int(f)) if f == int(f) else repr(round(f, 6)).rstrip("0").rstrip(".")


def numbers(text: str) -> set[str]:
    return {_norm(n) for n in _NUM.findall(text or "")}


def allowed_numbers(*data: Any) -> set[str]:
    blob = " ".join(d if isinstance(d, str) else json.dumps(d, default=str) for d in data)
    return numbers(blob)


def ungrounded(text: str, allowed: set[str]) -> list[str]:
    return sorted(n for n in numbers(text) if n not in allowed)
