"""Prompt library loader (AI-01-02).

System prompts live in the repo-root ``prompts/`` package as reviewed markdown,
not as Python string constants:

    prompts/base.md             domain-expert framing (references the knowledge pack)
    prompts/guardrails.md       rules every persona obeys
    prompts/voice.md            house voice with good/bad pairs
    prompts/personas/*.md       one thin overlay per persona
    prompts/variants/*.md       alternative overlay for a persona (e.g. analyst teaser)

``build_system_prompt(persona)`` assembles, in order::

    <!-- header: persona + sha -->
    base  +  guardrails  +  voice  +  persona overlay  +  knowledge sections

The result is cached per ``(persona, variant)`` and carries a stable sha in a
header comment, so a change in any source file (or in the knowledge pack) is
visible in the snapshot tests under ``tests/report/snapshots``.

Location: the ``prompts`` directory is found relative to this file (source
checkout) or via the ``SAILRATINGS_PROMPTS_DIR`` environment variable.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from irc_data.api.services.report.knowledge import load_knowledge

PROMPTS_ENV_VAR = "SAILRATINGS_PROMPTS_DIR"

SECTION_SEPARATOR = "\n\n---\n\n"

# Persona -> knowledge-pack sections injected after the persona overlay.
# ``None`` means the whole pack; an empty tuple means no knowledge section.
PERSONA_KNOWLEDGE: dict[str, tuple[int, ...] | None] = {
    "analyst": None,
    "report_writer": None,
    "race_prep": (1, 4, 5, 6, 7, 8),
    "programme_advisor": (1, 2, 3, 4, 6, 7, 8),
    "watch": (1, 2, 4, 8),
    "admin_chat": (1, 2, 3, 8),
    "extractor": (2, 3, 6),
}

PERSONAS: tuple[str, ...] = tuple(PERSONA_KNOWLEDGE)

# Personas that do not write prose for a human, so no house voice is injected.
_NO_VOICE = frozenset({"extractor"})

# variant name -> persona it is valid for
VARIANTS: dict[str, str] = {"teaser": "analyst"}


def prompts_dir() -> Path:
    """Return the ``prompts`` directory."""
    override = os.environ.get(PROMPTS_ENV_VAR)
    if override:
        return Path(override)
    # .../repo/api/src/irc_data/api/services/report/prompt_library.py -> repo
    return Path(__file__).resolve().parents[6] / "prompts"


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file missing: {path} (set {PROMPTS_ENV_VAR} if prompts/ is not next to the source tree)")
    return path.read_text(encoding="utf-8").strip()


def base_text() -> str:
    return _read(prompts_dir() / "base.md")


def guardrails_text() -> str:
    return _read(prompts_dir() / "guardrails.md")


def voice_text() -> str:
    return _read(prompts_dir() / "voice.md")


def persona_text(persona: str, variant: str | None = None) -> str:
    _check(persona, variant)
    if variant:
        return _read(prompts_dir() / "variants" / f"{variant}.md")
    return _read(prompts_dir() / "personas" / f"{persona}.md")


def _check(persona: str, variant: str | None) -> None:
    if persona not in PERSONA_KNOWLEDGE:
        raise KeyError(f"Unknown persona {persona!r}; expected one of {', '.join(PERSONAS)}")
    if variant is not None and VARIANTS.get(variant) != persona:
        raise KeyError(f"Unknown variant {variant!r} for persona {persona!r}")


def knowledge_text(persona: str) -> str:
    sections = PERSONA_KNOWLEDGE[persona]
    if sections is not None and not sections:
        return ""
    return load_knowledge(sections).strip()


@lru_cache(maxsize=None)
def build_system_prompt(persona: str, variant: str | None = None) -> str:
    """Assemble the system prompt for ``persona`` (optionally a ``variant``).

    base + guardrails + voice + persona overlay + knowledge sections, preceded by
    a header comment carrying a stable sha of the assembled body.
    """
    _check(persona, variant)
    parts = [base_text(), guardrails_text()]
    if persona not in _NO_VOICE:
        parts.append(voice_text())
    parts.append(persona_text(persona, variant))
    knowledge = knowledge_text(persona)
    if knowledge:
        parts.append("# KNOWLEDGE PACK\n\n" + knowledge)
    body = SECTION_SEPARATOR.join(parts) + "\n"
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    label = persona if variant is None else f"{persona}/{variant}"
    return f"<!-- prompt-library persona={label} sha={sha} -->\n\n{body}"


def clear_cache() -> None:
    """Drop cached prompts (tests, hot-reload of prompts/ or the knowledge pack)."""
    build_system_prompt.cache_clear()
