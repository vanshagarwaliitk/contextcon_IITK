"""The guided CLI wizard, driven with scripted answers (no real keys, no network)."""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `scripts`

from app.providers.crustdata.client import CrustdataClient  # noqa: E402
from conftest import ScriptedLLM  # noqa: E402
from scripts import wizard  # noqa: E402


def answers(*xs):
    it = iter(xs)
    return lambda prompt: next(it)


@pytest.fixture(autouse=True)
def tmp_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard, "EXPORTS", tmp_path / "exports")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    # never touch the real Claude Code CLI from tests
    monkeypatch.setattr(wizard.claude_cli.shutil, "which", lambda name: None)


def test_offers_claude_account_when_cli_installed(settings, monkeypatch):
    monkeypatch.setattr(wizard.claude_cli.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(wizard, "check_account", lambda s: True)
    s = replace(settings, anthropic_api_key="", llm_backend="auto")
    out = wizard.collect_keys(s, answers("", "y"))  # Enter = yes, use my account; then save to .env
    assert out.llm_backend == "claude_cli" and not out.fail_llm and out.anthropic_api_key == ""
    assert "LLM_BACKEND=claude_cli" in wizard.ENV_FILE.read_text()


def test_falls_back_to_key_prompt_when_account_login_fails(settings, monkeypatch):
    monkeypatch.setattr(wizard.claude_cli.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(wizard, "check_account", lambda s: False)
    s = replace(settings, anthropic_api_key="", llm_backend="auto")
    out = wizard.collect_keys(s, answers("y", "", "n"))  # yes account -> fails -> skip key -> don't save
    assert out.fail_llm is True


def test_rejects_gemini_and_openai_keys():
    assert "Gemini" in wizard.check_anthropic("AIzaSyFakeFakeFake")
    assert "OpenAI" in wizard.check_anthropic("sk-proj-abc")
    assert wizard.check_anthropic("sk-ant-api03-xyz") is None


def test_collect_keys_reasks_after_gemini_key_and_saves(settings):
    s = replace(settings, anthropic_api_key="", crustdata_api_key="")
    out = wizard.collect_keys(s, answers("AIzaWrongKey", "sk-ant-good-key-123", "crust-key-456", "y"))
    assert out.anthropic_api_key == "sk-ant-good-key-123" and out.crustdata_api_key == "crust-key-456"
    assert not out.fail_llm
    env = wizard.ENV_FILE.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-good-key-123" in env and "CRUSTDATA_API_KEY=crust-key-456" in env


def test_skipping_anthropic_key_gives_facts_only_mode(settings):
    s = replace(settings, anthropic_api_key="")
    out = wizard.collect_keys(s, answers("", "n"))
    assert out.fail_llm is True


def test_full_run_with_domain(db, settings, transport, capsys):
    crust = CrustdataClient(db=db, settings=settings, transport=transport, sleep=lambda s: None)
    inv = wizard.investigate(settings, answers("acme.com", "1", "peer.com"), open_browser=False,
                             crust=crust, llm=ScriptedLLM(settings))
    row = db.get_investigation(inv)
    assert row["user_question"] == wizard.EXAMPLES[0] and row["status"].startswith("completed")
    out = capsys.readouterr().out
    assert "[Crustdata]" in out and "Hypotheses" in out and "Brief saved to" in out
    assert list((wizard.EXPORTS).glob("coredata_*.html"))


def test_ambiguous_name_asks_to_pick(db, settings, transport, capsys):
    crust = CrustdataClient(db=db, settings=settings, transport=transport, sleep=lambda s: None)
    inv = wizard.investigate(settings, answers("Acme", "", "", "1"), open_browser=False,
                             crust=crust, llm=ScriptedLLM(settings))
    row = db.get_investigation(inv)
    assert row["resolved_company_id"] == 100 and row["status"].startswith("completed")
    assert "Which company did you mean?" in capsys.readouterr().out
