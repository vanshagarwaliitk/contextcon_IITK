"""Runtime settings, read from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # repo root
load_dotenv(ROOT / ".env")


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    crustdata_api_key: str = field(default_factory=lambda: os.getenv("CRUSTDATA_API_KEY", ""))
    crustdata_base_url: str = field(default_factory=lambda: os.getenv("CRUSTDATA_BASE_URL", "https://api.crustdata.com"))
    crustdata_api_version: str = "2025-11-01"
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: os.getenv("LLM_EFFORT", "medium"))
    llm_fallbacks: bool = field(default_factory=lambda: _bool("LLM_FALLBACKS", "1"))
    fail_llm: bool = field(default_factory=lambda: _bool("FAIL_LLM"))

    provider_mode: str = field(default_factory=lambda: os.getenv("PROVIDER_MODE", "live"))
    record: bool = field(default_factory=lambda: _bool("RECORD"))
    fixtures_dir: Path = field(default_factory=lambda: Path(os.getenv("FIXTURES_DIR", str(ROOT / "fixtures"))))

    credit_budget: float = field(default_factory=lambda: float(os.getenv("CREDIT_BUDGET", "150")))
    max_api_calls: int = field(default_factory=lambda: int(os.getenv("MAX_API_CALLS", "60")))
    max_probes: int = field(default_factory=lambda: int(os.getenv("MAX_PROBES", "25")))

    enable_people: bool = field(default_factory=lambda: _bool("ENABLE_PEOPLE", "1"))
    enable_web: bool = field(default_factory=lambda: _bool("ENABLE_WEB", "1"))

    fail_endpoints: list[str] = field(
        default_factory=lambda: [e.strip() for e in os.getenv("FAIL_ENDPOINT", "").split(",") if e.strip()]
    )
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "coredata.db")))


def get_settings() -> Settings:
    return Settings()
