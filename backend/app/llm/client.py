"""Claude wrapper — the "brain". Two entry points:

* structured(): one call -> validated pydantic object (structured outputs), with up to
  2 repair retries that feed Python validation errors back to the model.
* tool_turn(): one turn of a manual tool-use loop (used only by the probe planner).

Notes for claude-opus-5-5: thinking is adaptive and always on (we control depth with
output_config.effort), temperature is not accepted, and forced tool_choice returns 400,
so structured outputs are used instead of forcing a tool.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..db import DB, now_iso
from . import claude_cli

log = logging.getLogger("coredata.llm")
PROMPTS = Path(__file__).with_name("prompts")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """LLM unavailable / refused / failed validation after repairs."""


class LLMValidationError(LLMError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors)[:500])
        self.errors = errors


def render(prompt_file: str, variables: dict[str, Any]) -> str:
    text = (PROMPTS / prompt_file).read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        v = variables.get(m.group(1), "")
        return v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, text)


class LLM:
    def __init__(self, settings: Settings | None = None, db: DB | None = None, client: Any = None):
        self.settings = settings or get_settings()
        self.db = db
        self._client = client
        # "api" (Anthropic API key) | "claude_cli" (user's Claude account via Claude Code) | "none"
        self.backend = "api" if client is not None else claude_cli.resolve_backend(self.settings)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "backend": self.backend, "cost_usd": 0.0}

    @property
    def client(self) -> Any:
        if self.settings.fail_llm:
            raise LLMError("LLM disabled (FAIL_LLM=1)")
        if self.backend == "none":
            raise LLMError("no Claude available: set an Anthropic API key, or install Claude Code and log in "
                           "to use your Claude account")
        if self._client is None:
            options = {"max_retries": 2}
            if self.settings.anthropic_api_key:
                options["api_key"] = self.settings.anthropic_api_key
            self._client = anthropic.Anthropic(**options)
        return self._client

    def _extra(self) -> dict:
        if self.settings.llm_fallbacks:
            return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return {}

    def _track(self, resp: Any, label: str) -> None:
        u = getattr(resp, "usage", None)
        self.usage["calls"] += 1
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        log.info("llm %s stop=%s usage=%s", label, getattr(resp, "stop_reason", None), u)

    # ------------------------------------------------------------------ structured
    def structured(
        self,
        prompt_file: str,
        variables: dict[str, Any],
        model_cls: type[T],
        validator: Callable[[T], list[str]] | None = None,
        max_repairs: int = 2,
        effort: str | None = None,
        system: str | None = None,
    ) -> T:
        prompt = render(prompt_file, variables)
        if self.backend == "claude_cli" and self._client is None:
            return self._structured_cli(prompt_file, prompt, model_cls, validator, max_repairs, effort, system)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        errors: list[str] = []
        for attempt in range(max_repairs + 1):
            try:
                kwargs: dict[str, Any] = dict(
                    model=self.settings.llm_model,
                    max_tokens=16000,
                    output_config={"effort": effort or self.settings.llm_effort},
                    output_format=model_cls,
                    messages=messages,
                    **self._extra(),
                )
                if system:
                    kwargs["system"] = system
                resp = self.client.beta.messages.parse(**kwargs)
            except LLMError:
                raise
            except anthropic.APIError as exc:  # connection, status, timeout...
                raise LLMError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
            self._track(resp, prompt_file)

            if resp.stop_reason == "refusal":
                raise LLMError("model refused the request")
            parsed = getattr(resp, "parsed_output", None)
            if parsed is None:
                errors = [f"no parseable output (stop_reason={resp.stop_reason})"]
            else:
                errors = validator(parsed) if validator else []
                if not errors:
                    return parsed
            log.warning("llm %s attempt %d failed validation: %s", prompt_file, attempt + 1, errors)
            messages.append({"role": "assistant", "content": resp.content})
            messages.append(
                {
                    "role": "user",
                    "content": "Your previous output failed validation:\n- "
                    + "\n- ".join(errors[:15])
                    + "\nReturn a corrected, complete output that fixes every error. Follow all original rules.",
                }
            )
        raise LLMValidationError(errors)

    # ------------------------------------------------------------------ Claude account (CLI) backend
    def _structured_cli(self, label: str, prompt: str, model_cls: type[T], validator, max_repairs: int,
                        effort: str | None, system: str | None) -> T:
        if self.settings.fail_llm:
            raise LLMError("LLM disabled (FAIL_LLM=1)")
        schema = model_cls.model_json_schema()
        errors: list[str] = []
        attempt_prompt = prompt
        for attempt in range(max_repairs + 1):
            try:
                data, meta = claude_cli.run_structured(self.settings, attempt_prompt, schema,
                                                       effort or self.settings.llm_effort, system)
            except claude_cli.ClaudeCLIError as exc:
                raise LLMError(str(exc)) from exc
            self.usage["calls"] += 1
            self.usage["input_tokens"] += meta["input_tokens"] or 0
            self.usage["output_tokens"] += meta["output_tokens"] or 0
            self.usage["cost_usd"] = round(self.usage["cost_usd"] + meta["cost_usd"], 4)
            log.info("llm(cli) %s attempt %d %sms $%.4f", label, attempt + 1, meta["duration_ms"], meta["cost_usd"])
            try:
                parsed = model_cls.model_validate(data)
                errors = validator(parsed) if validator else []
            except ValueError as exc:  # pydantic ValidationError subclasses ValueError
                parsed, errors = None, [f"schema validation: {str(exc)[:400]}"]
            if not errors:
                return parsed
            log.warning("llm(cli) %s attempt %d failed validation: %s", label, attempt + 1, errors)
            # stateless CLI call: resend the task with the previous answer and the errors appended
            attempt_prompt = (
                f"{prompt}\n\n---\nYOUR PREVIOUS OUTPUT:\n{json.dumps(data, ensure_ascii=False)[:12000]}\n\n"
                "It failed validation:\n- " + "\n- ".join(errors[:15])
                + "\nReturn a corrected, complete output that fixes every error. Follow all original rules."
            )
        raise LLMValidationError(errors)

    # ------------------------------------------------------------------ tool loop turn
    def tool_turn(self, system: str, messages: list[dict], tools: list[dict], effort: str = "low") -> Any:
        try:
            resp = self.client.beta.messages.create(
                model=self.settings.llm_model,
                max_tokens=8000,
                system=system,
                tools=tools,
                output_config={"effort": effort},
                messages=messages,
                **self._extra(),
            )
        except LLMError:
            raise
        except anthropic.APIError as exc:
            raise LLMError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        self._track(resp, "planner")
        if resp.stop_reason == "refusal":
            raise LLMError("model refused the request")
        return resp
