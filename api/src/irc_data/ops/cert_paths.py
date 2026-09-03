"""Resolve ``irc_certificates.pdf_path`` values to files on disk.

Rows written before the monorepo cutover still store absolute paths under
the old backend repo (``/home/irc-data/code/irc-data/data/...``), which no
longer exists.  The PDFs themselves moved with the monorepo, and the
``historical/`` sub-directory was flattened for some certs, so resolution
tries, in order:

1. the stored path verbatim;
2. the stored path rebased onto each candidate cert-data root;
3. the basename under ``<root>/`` and ``<root>/historical/``.

Candidate roots come from ``IRC_CERT_DATA_DIRS`` (os.pathsep-separated) and
default to ``<api>/data/raw/certificates`` for every checkout that contains
this file (the source tree sits at ``<api>/src/irc_data/...``), followed by
the live deployment path.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOTS = [
    Path(__file__).resolve().parents[3] / "data" / "raw" / "certificates",
    Path("/home/irc-data/code/sailratings/api/data/raw/certificates"),
]

# Path fragments from pre-monorepo absolute paths that must be rebased.
_LEGACY_MARKERS = ("/data/raw/certificates/",)


def candidate_roots(extra: list[Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("IRC_CERT_DATA_DIRS")
    if env:
        roots.extend(Path(p) for p in env.split(os.pathsep) if p)
    if extra:
        roots.extend(extra)
    roots.extend(_DEFAULT_ROOTS)
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def resolve_cert_pdf(pdf_path: str | None, *, extra_roots: list[Path] | None = None) -> Path | None:
    """Return the on-disk path for a stored ``pdf_path``, or ``None``."""
    if not pdf_path:
        return None
    stored = Path(pdf_path)
    if stored.exists():
        return stored

    roots = candidate_roots(extra_roots)

    # Rebase legacy absolute paths: keep everything after the marker.
    for marker in _LEGACY_MARKERS:
        if marker in pdf_path:
            rel = pdf_path.split(marker, 1)[1]
            for root in roots:
                cand = root / rel
                if cand.exists():
                    return cand
            break  # marker matched; fall through to basename search

    # Basename fallback handles the historical/ flattening.
    name = stored.name
    for root in roots:
        for cand in (root / name, root / "historical" / name):
            if cand.exists():
                return cand
    return None
