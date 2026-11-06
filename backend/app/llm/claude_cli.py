"""Claude through the user's own logged-in Claude account (Claude Code CLI, headless `claude -p`).

No Anthropic API key needed: the CLI authenticates with the account the user logged into with
`claude` (`/login`). Calls are kept lean — our own system prompt replaces Claude Code's, all tools
and MCP servers are off, and nothing is saved as a session — and the CLI's `--json-schema` returns
a ready-parsed `structured_output`, which our pydantic models then validate.

Usage counts against the user's Claude plan limits. Personal / local use; deployments should use
an API key (LLM_BACKEND=api).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from ..config import Settings

SYSTEM_PROMPT = (
    "You are a module inside CoreData, a competitive-intelligence pipeline. "
    "Follow the task in the user message exactly and answer only through the structured output. "
    "Use only the data given in the message; do not use tools or outside knowledge about the company."
)
# Never let a stray/invalid key in the environment (e.g. a Gemini key in ANTHROPIC_API_KEY) override the account login.
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


class ClaudeCLIError(Exception):
    pass


def find_cli(settings: Settings) -> str | None:
    return shutil.which(settings.claude_cli_path)


def resolve_backend(settings: Settings) -> str:
    """api | claude_cli | none."""
    b = settings.llm_backend
    if b == "api":
        return "api"
    if b in ("claude_cli", "cli", "account"):
        return "claude_cli"
    if settings.anthropic_api_key.strip():
        return "api"
    return "claude_cli" if find_cli(settings) else "none"


def run_structured(settings: Settings, prompt: str, schema: dict, effort: str, system: str | None = None) -> tuple[dict, dict]:
    """One headless call. Returns (structured_output, meta). Raises ClaudeCLIError on any failure."""
    exe = find_cli(settings)
    if not exe:
        raise ClaudeCLIError("Claude Code CLI ('claude') not found. Install it and run `claude` once to log in.")
    args = [
        exe, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema, separators=(",", ":")),
        "--system-prompt", system or SYSTEM_PROMPT,
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--model", settings.llm_model,
        "--effort", effort,
    ]
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    try:
        proc = subprocess.run(
            args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=settings.claude_cli_timeout, env=env, cwd=tempfile.gettempdir(),  # no project CLAUDE.md
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCLIError(f"Claude CLI timed out after {settings.claude_cli_timeout}s") from exc
    except OSError as exc:
        raise ClaudeCLIError(f"could not start Claude CLI: {exc}") from exc
    out = (proc.stdout or "").strip()
    try:
        data: dict[str, Any] = json.loads(out.splitlines()[-1] if out else "{}")
    except ValueError:
        data = {}
    if proc.returncode != 0 or data.get("is_error") or not data:
        detail = data.get("result") or (proc.stderr or out or "no output").strip()
        if "login" in detail.lower() or "auth" in detail.lower():
            detail += " — open a terminal, run `claude`, and log in with your Claude account."
        raise ClaudeCLIError(f"Claude CLI failed (exit {proc.returncode}): {detail[:300]}")
    structured = data.get("structured_output")
    if structured is None:
        try:
            structured = json.loads(data.get("result") or "")
        except ValueError as exc:
            raise ClaudeCLIError("Claude CLI returned no structured output") from exc
    meta = {
        "cost_usd": float(data.get("total_cost_usd") or 0),
        "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
        "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
        "duration_ms": data.get("duration_ms"),
    }
    return structured, meta
