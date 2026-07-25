"""Gemini wrapper for per-section generation + truth-discipline scanner.

The scanner extracts numeric tokens from generated markdown and checks
they appear in a Facts-derived allowlist. Tokens outside the allowlist
are not blocked — they're logged with the section context so we can
audit and tighten prompts. Hard-blocking would risk dropping legitimate
prose; logging gives us the signal we need without false negatives.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Match positive decimals, integers, and percentages. Skip 1- and 2-digit
# integers (years, race numbers, fleet positions — too many false positives).
_NUMERIC_RE = re.compile(r"\b\d{3,}(?:[.,]\d+)?%?\b|\b\d+\.\d+%?\b")


def extract_numeric_tokens(text: str) -> set[str]:
    """Pull numeric-looking tokens out of generated prose.

    Returns RAW token strings as they appear in the prose (sans
    trailing % and thousand separators). Normalisation is the audit
    step's job — keeping raw tokens here means callers can also see
    exactly what Claude wrote in any logged comparison.
    """
    out: set[str] = set()
    for m in _NUMERIC_RE.finditer(text or ""):
        tok = m.group(0).rstrip("%").replace(",", "")
        out.add(tok)
    return out


def _normalise_number(s: str) -> str:
    """Normalise '1.0250' and '1.025' to a single representation."""
    try:
        d = Decimal(s)
        # Strip trailing zeros after the decimal point.
        normalised = d.normalize()
        # Decimal.normalize() can produce scientific notation for tiny
        # numbers — convert to plain string.
        return format(normalised, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return s


def facts_numeric_allowlist(facts: Any, *, round_to: int = 4) -> set[str]:
    """Walk a Facts dataclass tree and collect every numeric value as
    a set of normalised string tokens the prose may legitimately cite.

    Nested dataclasses, lists, dicts and Decimals are all walked.
    """
    allow: set[str] = set()

    def _add(v: Any) -> None:
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, (int, float, Decimal)):
            try:
                d = Decimal(str(v))
                # Add signed and unsigned forms — prose may cite the
                # magnitude of a negative delta as a positive number
                # ("285 kg lighter") instead of "-285".
                for variant in (d, abs(d)):
                    allow.add(_normalise_number(format(variant, "f")))
                    # Also add common roundings the model is likely to use.
                    for places in (0, 1, 2, 3, 4):
                        allow.add(_normalise_number(
                            format(round(float(variant), places), "f")
                        ))
            except Exception:
                pass
        elif isinstance(v, str):
            return
        elif is_dataclass(v):
            for fname in v.__dataclass_fields__:
                _add(getattr(v, fname))
        elif isinstance(v, dict):
            for vv in v.values():
                _add(vv)
        elif isinstance(v, (list, tuple, set)):
            for vv in v:
                _add(vv)

    _add(facts)
    return allow


def audit_section_numbers(markdown: str, facts: Any, *, section_id: str) -> dict:
    """Compare numeric tokens in generated prose against the Facts allowlist.

    Returns a dict with `suspicious` (tokens NOT in allowlist) and
    `cited` (tokens that matched). Suspicious tokens are logged but
    the section is not blocked — a numbers-policed prompt should
    produce close to zero suspicious tokens, so any spike is signal.
    """
    raw_seen = extract_numeric_tokens(markdown)
    allow = facts_numeric_allowlist(facts)
    # Normalise extracted tokens for matching (so "1.0250" matches "1.025").
    suspicious: list[str] = []
    cited: list[str] = []
    for tok in sorted(raw_seen):
        if _normalise_number(tok) in allow:
            cited.append(tok)
        else:
            suspicious.append(tok)
    if suspicious:
        logger.warning(
            "[%s] Suspicious numeric tokens in generated prose: %s",
            section_id, suspicious,
        )
    return {"suspicious": suspicious, "cited": cited, "allow_size": len(allow)}


def call_gemini(*, system: str, user: str, max_tokens: int = 2000,
                section_id: str = "?") -> str:
    """One Gemini call. Centralised so we can swap model / add caching."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        )
    )
    return (resp.text or "").strip()


# For backward compatibility with section files
def call_claude(*args, **kwargs):
    return call_gemini(*args, **kwargs)
