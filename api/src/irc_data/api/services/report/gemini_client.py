"""Strict-mode entry point for the numeric audit (AI-01-03).

The numeric-token mechanism lives in ``claude_client`` (the module that
``gemini_client`` was renamed to when the provider layer moved to
``irc_data.llm``). This module adds the ``strict`` flag without changing the
default behaviour of the existing function.
"""
from __future__ import annotations

from typing import Any

from irc_data.api.services.report import claude_client
from irc_data.api.services.report.grounding import GroundingError

extract_numeric_tokens = claude_client.extract_numeric_tokens
facts_numeric_allowlist = claude_client.facts_numeric_allowlist


def audit_section_numbers(
    markdown: str, facts: Any, *, section_id: str, strict: bool = False,
) -> dict:
    """Audit numeric tokens in ``markdown`` against the facts allowlist.

    ``strict=False`` (default): identical to ``claude_client.audit_section_numbers``
    — suspicious tokens are logged, nothing raised.
    ``strict=True``: any suspicious token raises ``GroundingError``.
    """
    result = claude_client.audit_section_numbers(markdown, facts, section_id=section_id)
    if strict and result["suspicious"]:
        raise GroundingError(result["suspicious"], section_id=section_id)
    return result
