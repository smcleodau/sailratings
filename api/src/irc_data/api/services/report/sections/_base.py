"""Shared section primitives — every section emits a SectionResult."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SectionResult:
    """What one section function returns to the orchestrator."""
    section_id: str            # 's03_rating_anatomy'
    title: str                 # 'Rating Anatomy'
    markdown: str              # the prose body
    chart_pngs: dict[str, bytes] = field(default_factory=dict)
    # ↑ keyed by stable slot name e.g. 'anatomy_bar', referenced by the
    #   HTML template; the orchestrator base64-inlines each one.
    structured: dict = field(default_factory=dict)
    # ↑ machine-readable snapshot (the Facts dict) for the
    #   report_analytics JSONB column.
    error: str | None = None
