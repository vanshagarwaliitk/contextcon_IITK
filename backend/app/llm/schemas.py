"""Pydantic schemas for every structured LLM output (structured-outputs compatible:
no free-form dicts, everything explicit)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..models import HypothesisType, Theme


# B1 ------------------------------------------------------------------
class ScopingResult(BaseModel):
    themes: list[Theme]


# B3 ------------------------------------------------------------------
class ClusterOut(BaseModel):
    cluster_id: str
    label: str
    theme_id: str
    signal_ids: list[str]
    why_related: str
    noise_signal_ids: list[str]


class ClusterResult(BaseModel):
    clusters: list[ClusterOut]


# B4 ------------------------------------------------------------------
class Alternative(BaseModel):
    ref: Literal["H2", "H3"]
    statement: str
    would_be_supported_by: str
    consistent_signal_ids: list[str]


class MatrixCell(BaseModel):
    signal_id: str
    hypothesis_ref: Literal["H1", "H2", "H3"]
    relation: Literal["consistent", "inconsistent", "neutral"]


class HypothesisOut(BaseModel):
    hypothesis_type: HypothesisType
    title: str
    statement: str
    supporting_signal_ids: list[str]
    contradicting_signal_ids: list[str]
    alternatives: list[Alternative]
    evidence_gaps: list[str]
    recommended_next_indicators: list[str]
    matrix: list[MatrixCell]
    diagnostic_note: str


class HypothesisResult(BaseModel):
    hypotheses: list[HypothesisOut]


# B5 ------------------------------------------------------------------
TestId = Literal[
    "novelty_test", "evergreen_test", "scale_test", "peer_test",
    "seniority_test", "geo_test", "corroboration_test", "source_diversity_test",
]


class TestPick(BaseModel):
    test_id: str  # validated in Python (unknown ids dropped)
    reason: str


class FalsifyPlan(BaseModel):
    tests: list[TestPick]
    disconfirming_web_queries: list[str]


# B6 ------------------------------------------------------------------
class Verdict(BaseModel):
    claim_id: str
    verdict: Literal["supported", "partial", "unsupported", "contradicted"]
    reason: str


class VerifyResult(BaseModel):
    verdicts: list[Verdict]


# B7 ------------------------------------------------------------------
class BriefSentence(BaseModel):
    text: str
    claim_ids: list[str]


class BriefOut(BaseModel):
    executive_summary: list[BriefSentence]
    key_findings: list[BriefSentence]
    hypotheses: list[BriefSentence]
    counter_evidence_and_alternatives: list[BriefSentence]
    unknowns: list[BriefSentence]
    recommended_next_indicators: list[BriefSentence]
