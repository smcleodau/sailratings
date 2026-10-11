"""IN-01-02 — IntelligenceAnswerV1: the versioned answer envelope.

Every fact served by ``GET /v1/intelligence/*`` leaves the API inside an
:class:`IntelligenceAnswerV1` carrying a :class:`Provenance` block (dataset
version, facts hash, lineage refs, freshness, quality status) or an explicit
``incomplete=True`` answer naming what is missing. See SPEC-25 §2.

IN-01-03 (ConfidenceAssessmentV1) and IN-01-05 append to this file after the
definitions below; ``IntelligenceAnswerV1.confidence`` is the placeholder
IN-01-03 fills in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

try:  # AI-01-03 owns EvidenceRef in schemas/analyst.py; reuse once it lands.
    from irc_data.api.schemas.analyst import EvidenceRef  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - analyst.py does not exist yet

    class EvidenceRef(BaseModel):  # type: ignore[no-redef]
        """Local stand-in for ``schemas.analyst.EvidenceRef`` (AI-01-03).

        ``source`` names the table/view/bundle section a fact came from and
        ``ref`` is a stable locator inside it (e.g. ``boats:id=17213``).
        """

        source: str
        ref: str
        label: str | None = None
        value: Any | None = None


QualityStatus = Literal["ok", "degraded", "incomplete"]

SCHEMA_VERSION = "IntelligenceAnswerV1"


class Provenance(BaseModel):
    """Where an answer's facts came from and how far to trust them."""

    dataset_version: str
    facts_sha: str
    lineage: list[str] = Field(default_factory=list)
    freshness_at: datetime | None = None
    quality_status: QualityStatus


class IntelligenceAnswerV1(BaseModel):
    """A named, versioned query answer with provenance attached."""

    query: str
    version: str
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    incomplete: bool = False
    missing: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Placeholder: ConfidenceAssessmentV1 is defined and populated by IN-01-03.
    confidence: dict[str, Any] | None = None
