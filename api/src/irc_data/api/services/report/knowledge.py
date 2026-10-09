"""Loader for the reviewed sailing knowledge pack (AI-01-01).

The pack lives in the repo, not in prompt strings:

    docs/ai/knowledge/NN-*.md   eight numbered sections
    docs/ai/glossary.yaml       term / definition / unit / aliases / source

Public API
----------
``load_knowledge(sections=None) -> str``
    Concatenated markdown of the requested sections (all eight when
    ``sections`` is None). ``sections`` may hold section numbers (``1``),
    number prefixes (``"01"``) or full stems (``"01-rating-systems"``).
    Output is in section order regardless of request order.

``load_glossary() -> dict``
    The parsed ``glossary.yaml`` (``{"terms": [...]}``).

Both are cached for the life of the process; call ``clear_cache()`` in tests
or after editing the pack on disk.

Location: the repo's ``docs/ai`` directory is found relative to this file
(source checkout) or via the ``SAILRATINGS_AI_DOCS_DIR`` environment
variable (deployments that do not ship the whole repo).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

DOCS_ENV_VAR = "SAILRATINGS_AI_DOCS_DIR"

_SECTION_SEPARATOR = "\n\n---\n\n"


def docs_dir() -> Path:
    """Return the ``docs/ai`` directory."""
    override = os.environ.get(DOCS_ENV_VAR)
    if override:
        return Path(override)
    # .../repo/api/src/irc_data/api/services/report/knowledge.py -> repo
    return Path(__file__).resolve().parents[6] / "docs" / "ai"


def knowledge_dir() -> Path:
    return docs_dir() / "knowledge"


def glossary_path() -> Path:
    return docs_dir() / "glossary.yaml"


def section_files() -> list[Path]:
    """All knowledge section files in numeric order."""
    files = sorted(knowledge_dir().glob("[0-9][0-9]-*.md"))
    if not files:
        raise FileNotFoundError(
            f"No knowledge sections found in {knowledge_dir()} "
            f"(set {DOCS_ENV_VAR} if the docs are not next to the source tree)"
        )
    return files


def _matches(path: Path, selector: int | str) -> bool:
    if isinstance(selector, int):
        return path.name.startswith(f"{selector:02d}-")
    sel = str(selector)
    if sel.isdigit():
        return path.name.startswith(sel.zfill(2) + "-")
    return path.stem == sel or path.name == sel


@lru_cache(maxsize=None)
def _load(selectors: tuple[int | str, ...] | None) -> str:
    files = section_files()
    if selectors is not None:
        chosen = []
        for sel in selectors:
            hits = [f for f in files if _matches(f, sel)]
            if not hits:
                raise KeyError(f"Unknown knowledge section: {sel!r}")
            chosen.extend(h for h in hits if h not in chosen)
        files = [f for f in files if f in chosen]
    return _SECTION_SEPARATOR.join(f.read_text(encoding="utf-8").strip() for f in files) + "\n"


def load_knowledge(sections: Iterable[int | str] | None = None) -> str:
    """Return the knowledge pack (or the requested sections) as one string."""
    if sections is None:
        return _load(None)
    if isinstance(sections, (int, str)):
        sections = [sections]
    return _load(tuple(sections))


@lru_cache(maxsize=1)
def load_glossary() -> dict:
    """Return the parsed glossary: ``{"terms": [{term, definition, unit, aliases, source}, ...]}``."""
    with glossary_path().open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "terms" not in data:
        raise ValueError(f"{glossary_path()} must be a mapping with a 'terms' list")
    return data


def clear_cache() -> None:
    """Drop cached knowledge and glossary (tests, hot-reload)."""
    _load.cache_clear()
    load_glossary.cache_clear()
