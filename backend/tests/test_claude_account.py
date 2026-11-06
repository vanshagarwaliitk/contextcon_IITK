"""Claude-account backend (Claude Code CLI): subprocess faked, no real calls."""
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.llm import claude_cli
from app.llm.client import LLM, LLMError, LLMValidationError
from app.llm.schemas import PlannerBatch, ScopingResult
from app.models import Theme


def _theme(**kw):
    d = dict(id="t1", label="robotics", rationale="r", source="user_question", title_keywords=["robotics"],
             description_phrases=["motion planning"], anti_keywords=[], tech_keywords=[], role_families=[])
    d.update(kw)
    return d


class FakeCLI:
    """Stands in for subprocess.run(claude -p ...). Queue of structured outputs (or exceptions/err dicts)."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append({"args": args, **kw})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, dict) and out.get("_error"):
            return SimpleNamespace(returncode=1, stdout=json.dumps({"is_error": True, "result": out["_error"]}), stderr="")
        payload = {"is_error": False, "result": json.dumps(out), "structured_output": out, "total_cost_usd": 0.01,
                   "usage": {"input_tokens": 10, "output_tokens": 5}, "duration_ms": 900}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


@pytest.fixture
def cli(settings, monkeypatch):
    s = replace(settings, anthropic_api_key="", llm_backend="claude_cli")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "C:/fake/claude.exe")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "AIzaGeminiKeyThatMustNotLeak")
    return s


def test_resolve_backend(settings, monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    assert claude_cli.resolve_backend(replace(settings, anthropic_api_key="", llm_backend="auto")) == "none"
    assert claude_cli.resolve_backend(replace(settings, anthropic_api_key="sk-ant-x", llm_backend="auto")) == "api"
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "claude")
    assert claude_cli.resolve_backend(replace(settings, anthropic_api_key="", llm_backend="auto")) == "claude_cli"
    assert claude_cli.resolve_backend(replace(settings, anthropic_api_key="sk-ant-x", llm_backend="claude_cli")) == "claude_cli"


def test_structured_call_is_lean_and_strips_keys(cli, monkeypatch):
    fake = FakeCLI([{"themes": [_theme()]}])
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    llm = LLM(settings=cli)
    assert llm.backend == "claude_cli"
    out = llm.structured("b1_scoping.md", {"emerging_terms": []}, ScopingResult)
    assert out.themes[0].label == "robotics"
    call = fake.calls[0]
    args = call["args"]
    for flag in ("-p", "--json-schema", "--system-prompt", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--tools") + 1] == ""
    assert "ANTHROPIC_API_KEY" not in call["env"]          # never the Gemini key: use the account login
    assert "motion planning" not in call["input"] and "THEMES" in call["input"].upper()  # prompt goes via stdin
    assert llm.usage["calls"] == 1 and llm.usage["cost_usd"] == 0.01


def test_repair_loop_resends_errors(cli, monkeypatch):
    bad = {"themes": [_theme(description_phrases=["ai"])]}
    good = {"themes": [_theme()]}
    fake = FakeCLI([bad, good])
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    v = lambda r: ["generic phrase"] if r.themes[0].description_phrases == ["ai"] else []
    LLM(settings=cli).structured("b1_scoping.md", {}, ScopingResult, validator=v)
    assert len(fake.calls) == 2 and "failed validation" in fake.calls[1]["input"] and "generic phrase" in fake.calls[1]["input"]


def test_schema_mismatch_counts_as_validation_error(cli, monkeypatch):
    monkeypatch.setattr(claude_cli.subprocess, "run", FakeCLI([{"nope": 1}] * 3))
    with pytest.raises(LLMValidationError):
        LLM(settings=cli).structured("b1_scoping.md", {}, ScopingResult)


def test_cli_failures_become_llm_errors(cli, monkeypatch):
    monkeypatch.setattr(claude_cli.subprocess, "run", FakeCLI([{"_error": "Not logged in. Please run /login"}]))
    with pytest.raises(LLMError, match="log in"):
        LLM(settings=cli).structured("b1_scoping.md", {}, ScopingResult)
    monkeypatch.setattr(claude_cli.subprocess, "run", FakeCLI([subprocess.TimeoutExpired("claude", 5)]))
    with pytest.raises(LLMError, match="timed out"):
        LLM(settings=cli).structured("b1_scoping.md", {}, ScopingResult)


def test_missing_cli_and_no_key_is_clear_error(settings, monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    llm = LLM(settings=replace(settings, anthropic_api_key="", llm_backend="auto"))
    assert llm.backend == "none"
    with pytest.raises(LLMError, match="Claude Code"):
        llm.client


def test_batched_planner_runs_steps_through_guards(db, settings, transport, monkeypatch):
    from app.pipeline.context import Ctx
    from app.pipeline.planner import Planner
    from app.models import save_themes
    from app.providers.crustdata.client import CrustdataClient

    inv = db.create_investigation(None, "acme.com", "q", [])
    db.update_investigation(inv, resolved_company_id=100)
    crust = CrustdataClient(db=db, settings=settings, transport=transport)

    class BatchLLM(LLM):
        def __init__(self, s):
            super().__init__(settings=s, client=object())
            self.backend = "claude_cli"
            self.turn = 0

        def structured(self, prompt_file, variables, model_cls, **kw):
            assert prompt_file == "b2b_planner_json.md" and model_cls is PlannerBatch
            self.turn += 1
            if self.turn == 1:
                return PlannerBatch(steps=[
                    {"tool": "job_theme_count", "theme_id": "t1", "window": "recent", "reason": "cheap first"},
                    {"tool": "job_theme_count", "theme_id": "t1", "window": "recent", "reason": "duplicate"},
                    {"tool": "job_theme_rows", "theme_id": "t9", "reason": "bad theme"},
                ], stop=False, stop_reason="")
            assert any(h.get("ok") is False and "duplicate" in json.dumps(h) for h in variables["history"])
            return PlannerBatch(steps=[], stop=True, stop_reason="done")

    ctx = Ctx.load(db, inv, crust, BatchLLM(settings), settings)
    save_themes(db, inv, [Theme(**_theme())])
    Planner(ctx).run_batched()
    assert ctx.ledger_for("t1")["count"]["recent"] == 12
    msgs = [e["message"] for e in db.events_after(inv)]
    assert any(m.startswith("decide job_theme_count(t1)") for m in msgs) and any(m.startswith("stop") for m in msgs)


def test_server_rejects_gemini_key_and_accepts_account(settings, monkeypatch):
    from fastapi import HTTPException
    from pydantic import SecretStr
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: replace(settings, anthropic_api_key="", llm_backend="auto"))
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "claude")
    with pytest.raises(HTTPException) as e:
        main._settings_for_request(main.RunInvestigation(anthropic_api_key=SecretStr("AIzaXYZ")))
    assert "Gemini" in e.value.detail
    s = main._settings_for_request(main.RunInvestigation())
    assert claude_cli.resolve_backend(s) == "claude_cli"
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    with pytest.raises(HTTPException):
        main._settings_for_request(main.RunInvestigation())
