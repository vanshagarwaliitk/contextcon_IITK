"""Hypothesis validators, the repair loop of the real LLM client, scoping rules, brief validator."""
from types import SimpleNamespace

import pytest

from app.llm.client import LLM, LLMValidationError
from app.llm.schemas import Alternative, BriefOut, BriefSentence, HypothesisOut, HypothesisResult, MatrixCell, ScopingResult
from app.models import Signal, Theme
from app.pipeline import guards, hypothesize, scoping
from app.pipeline.brief import validate_brief


def _h(stmt="Signals suggest a robotics build-out.", sup=("s1", "s2"), **kw):
    cells = [MatrixCell(signal_id=s, hypothesis_ref=r, relation="neutral") for s in sup for r in ("H1", "H2", "H3")]
    base = dict(hypothesis_type="capability_buildout", title="Robotics", statement=stmt, supporting_signal_ids=list(sup),
                contradicting_signal_ids=[], alternatives=[
                    Alternative(ref="H2", statement="Existing product support.", would_be_supported_by="x", consistent_signal_ids=[]),
                    Alternative(ref="H3", statement="Evergreen reposts.", would_be_supported_by="y", consistent_signal_ids=[])],
                evidence_gaps=[], recommended_next_indicators=[], matrix=cells, diagnostic_note="")
    base.update(kw)
    return HypothesisResult(hypotheses=[HypothesisOut(**base)])


SIGS = {i: Signal(id=i, investigation_id="inv", signal_type="hiring_cluster", title=i, summary=i) for i in ("s1", "s2")}


def errs(res, nums=frozenset({"12"}), falling=False):
    return hypothesize.validate(res, set(SIGS), SIGS, set(nums), falling)


def test_valid_passes():
    assert errs(_h()) == []


def test_ungrounded_number_rejected():
    assert any("ungrounded" in e for e in errs(_h("Signals suggest 40 robotics postings.")))
    assert errs(_h("Signals suggest 12 robotics postings.")) == []


def test_fake_signal_id_rejected():
    assert any("unknown" in e for e in errs(_h(sup=("s1", "sig_999"))))


def test_banned_phrase_rejected():
    assert any("banned" in e for e in errs(_h("Signals suggest they will launch robots.")))


def test_safe_start_required():
    assert any("must start" in e for e in errs(_h("The company does robotics.")))


def test_falling_requires_contraction():
    assert any("contraction" in e for e in errs(_h(), falling=True))


def test_numbers_ignore_signal_ids():
    assert guards.numbers("see sig_014 and 3.50%") == {"3.5"}


class FakeAnthropic:
    """Mimics client.beta.messages.parse; returns queued parsed outputs."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out, content=[{"type": "text", "text": "{}"}],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_repair_loop_then_success(settings):
    bad, good = _h("Signals suggest 40 postings."), _h()
    fake = FakeAnthropic([bad, good])
    llm = LLM(settings=settings, client=fake)
    v = lambda r: errs(r)
    out = llm.structured("b4_hypothesis.md", {}, HypothesisResult, validator=v)
    assert out == good and len(fake.calls) == 2
    assert "failed validation" in fake.calls[1]["messages"][-1]["content"]
    assert fake.calls[0]["model"] == settings.llm_model and "temperature" not in fake.calls[0]


def test_fallback_after_two_repairs(settings):
    bad = _h("Signals suggest 40 postings.")
    llm = LLM(settings=settings, client=FakeAnthropic([bad, bad, bad]))
    with pytest.raises(LLMValidationError):
        llm.structured("b4_hypothesis.md", {}, HypothesisResult, validator=lambda r: errs(r))
    fb = hypothesize.fallback_hypothesis({"label": "x"}, "bad")
    assert fb.hypothesis_type == "insufficient_evidence" and guards.starts_safe(fb.statement)


def _theme(**kw):
    d = dict(id="t1", label="robotics", rationale="r", source="user_question", title_keywords=["robotics"],
             description_phrases=["motion planning"], anti_keywords=[], tech_keywords=[], role_families=[])
    d.update(kw)
    return Theme(**d)


def test_scoping_rules():
    assert any("generic" in e for e in scoping.validate(ScopingResult(themes=[_theme(description_phrases=["ai"])]), []))
    assert any("data_driven" in e for e in scoping.validate(ScopingResult(themes=[_theme()]), [{"term": "x"}]))
    assert scoping.validate(ScopingResult(themes=[_theme(), _theme(id="t2", source="data_driven")]), [{"term": "x"}]) == []
    fb = scoping.fallback_themes("Are they moving into robotics?", [{"term": "perception"}])
    assert fb[0].title_keywords == ["robotics"] and fb[1].source == "data_driven"


def test_brief_validator():
    claims = {"clm_001": {"text": "12 postings", "status": "verified"}, "clm_002": {"text": "gap", "status": "unverified"}}
    def b(**kw):
        base = {k: [] for k in BriefOut.model_fields}
        base.update(kw)
        return BriefOut(**base)
    assert validate_brief(b(key_findings=[BriefSentence(text="12 postings", claim_ids=["clm_001"])]), claims) == []
    assert validate_brief(b(key_findings=[BriefSentence(text="gap", claim_ids=["clm_002"])]), claims)
    assert validate_brief(b(key_findings=[BriefSentence(text="99 postings", claim_ids=["clm_001"])]), claims)
    assert validate_brief(b(unknowns=[BriefSentence(text="Not verified: gap", claim_ids=["clm_002"])]), claims) == []
