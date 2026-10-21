"""AnalystAnswerV1 — the output contract for grounded analyst answers (AI-01-03).

Every number an analyst answer states must be traceable to evidence.
``irc_data.api.services.report.grounding.validate_grounded`` enforces that;
this module only defines the shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceKind = Literal[
    "cert_field", "regression", "results_set", "polar", "rule_note", "knowledge",
]


class EvidenceRef(BaseModel):
    """Pointer to the datum a claim rests on."""

    kind: EvidenceKind
    ref: str = Field(..., min_length=1)
    version: str | None = None


class Claim(BaseModel):
    """One statement; ``value``/``unit`` carry the headline number, if any."""

    text: str
    value: float | None = None
    unit: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class AnalystAnswerV1(BaseModel):
    """Structured analyst answer.

    ``confidence`` is a placeholder dict until ConfidenceAssessmentV1 (IN-01-03)
    lands.
    """

    claims: list[Claim] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: dict = Field(default_factory=dict)
    insufficient_data: bool = False
    question: str
