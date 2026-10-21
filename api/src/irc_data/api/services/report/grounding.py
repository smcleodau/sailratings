"""Strict grounding validation: no number without evidence (AI-01-03).

Unlike ``audit_section_numbers`` (which logs), ``validate_grounded`` raises.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from irc_data.api.schemas.analyst import AnalystAnswerV1
from irc_data.api.services.report.claude_client import (
    _normalise_number,
    extract_numeric_tokens,
    facts_numeric_allowlist,
)


class GroundingError(ValueError):
    """Raised when an answer states numbers that are not grounded.

    ``ungrounded_tokens``: every offending numeric token (text tokens and
    claim values, as strings). ``claims_without_evidence``: indices of claims
    that carry a ``value`` but no evidence.
    """

    def __init__(
        self,
        ungrounded_tokens: list[str] | None = None,
        claims_without_evidence: list[int] | None = None,
        *,
        section_id: str | None = None,
    ) -> None:
        self.ungrounded_tokens = list(ungrounded_tokens or [])
        self.claims_without_evidence = list(claims_without_evidence or [])
        self.section_id = section_id
        parts: list[str] = []
        if self.ungrounded_tokens:
            parts.append(f"numbers not in allowlist: {self.ungrounded_tokens}")
        if self.claims_without_evidence:
            parts.append(
                f"claims with value but no evidence: {self.claims_without_evidence}"
            )
        prefix = f"[{section_id}] " if section_id else ""
        super().__init__(prefix + "ungrounded answer — " + "; ".join(parts))


def build_allowlist(facts: Any) -> set[str]:
    """Allowlist of normalised numeric tokens derived from a facts tree."""
    return facts_numeric_allowlist(facts)


def _value_token(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def validate_grounded(answer: AnalystAnswerV1, allowlist: set[str]) -> None:
    """Raise GroundingError unless every claim number is in ``allowlist``
    and every claim with a ``value`` cites evidence. Returns None on success.
    """
    bad_tokens: list[str] = []
    no_evidence: list[int] = []

    def _flag(tok: str) -> None:
        if tok not in bad_tokens:
            bad_tokens.append(tok)

    for i, claim in enumerate(answer.claims):
        for tok in sorted(extract_numeric_tokens(claim.text)):
            if _normalise_number(tok) not in allowlist:
                _flag(tok)
        if claim.value is not None:
            if not math.isfinite(claim.value):
                _flag(str(claim.value))
            else:
                norm = _normalise_number(format(Decimal(repr(claim.value)), "f"))
                if norm not in allowlist:
                    _flag(_value_token(claim.value))
            if not claim.evidence:
                no_evidence.append(i)

    if bad_tokens or no_evidence:
        raise GroundingError(bad_tokens, no_evidence)
