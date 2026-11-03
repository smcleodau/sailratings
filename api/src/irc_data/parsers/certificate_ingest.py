"""Parse IRC certificate PDFs on disk into ``irc_certificates``.

Shared by ``irc-data parse-certs`` and the Temporal ``irc-certs`` runner so
both persist the same way.  History of what went wrong here (Nov 2026):

* ``upsert_certificate`` named the pre-0012 constraint
  (``certificates_cert_number_key``), so every upsert raised and nothing
  parsed after the table rename landed (newest row 2026-05-17).
* the CLI read ``cert.displacement_kg`` (the schema field is
  ``displacement``) and wrote a ``displacement`` key (the column is
  ``displacement_kg``), and one bad row aborted the whole run.
* Wayback-harvested non-certificates in ``historical/`` fell back to their
  wayback-timestamp filename prefix as a "cert number".
* boats created by later TCC imports were never linked to certs parsed
  earlier, leaving ~1,450 certificates with ``boat_id`` NULL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.parsers.certificate_pdf import (
    NotACertificate,
    parse_certificate_pdf,
    parse_filename_info,
)
from irc_data.parsers.schemas import CertificateData

logger = logging.getLogger(__name__)

# Real IRC cert numbers are short integers (TCC listing "Cert No").  Wayback
# filenames start with a 14-digit capture timestamp, which must never be
# taken for one.
_CERT_NO_RE = re.compile(r"^\d{1,7}$")

# CertificateData field -> irc_certificates column (identity unless listed).
_FIELD_TO_COLUMN = {"displacement": "displacement_kg"}
_CERT_FIELDS = (
    "issue_date", "source", "source_url", "lh", "beam", "draft", "displacement",
    "bo", "so", "p", "e", "j", "fl", "stl", "spl", "rig_type", "mast_material",
    "spreaders", "muw", "mtw", "mhw", "hlu", "hlp", "hhw", "htw", "huw",
    "sym_slu", "sym_sle", "sym_sf", "sym_shw", "asym_slu", "asym_sle", "asym_sf",
    "asym_shw", "water_ballast", "stix", "avs", "design_category", "lwp", "dlr",
    "x", "y", "internal_ballast", "hsa", "headsails_max", "flying_headsails_max",
    "fsa", "flu", "flp", "fuw", "ftw", "fhw", "fsfl", "fshw", "spa",
    "spinnakers_max", "stl_fh_max", "aft_rigging", "raw_data",
)


@dataclass
class IngestStats:
    files_seen: int = 0
    skipped_unchanged: int = 0
    parsed: int = 0
    parse_errors: int = 0
    not_certificate: int = 0
    inserted: int = 0
    updated: int = 0
    kept_newer: int = 0
    db_errors: int = 0
    matched: int = 0
    linked_after: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["errors"] = self.errors[:20]
        return d


def is_pdf_file(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5).startswith(b"%PDF")
    except OSError:
        return False


def iter_certificate_files(
    dirs: Iterable[Path], *, raw_store_dirs: Iterable[Path] = ()
) -> list[Path]:
    """Certificate PDFs to consider, oldest first (so the newest copy wins).

    ``dirs`` are flat download directories (``*.pdf``, not recursive, so
    ``superseded/`` archives and ``historical/`` stay out unless asked for).
    ``raw_store_dirs`` are content-addressed stores (extension-less files
    nested by hash prefix) that are walked recursively.
    """
    files: dict[Path, float] = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.glob("*.pdf"):
            if p.is_file():
                files[p.resolve()] = p.stat().st_mtime
    for d in raw_store_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and not p.name.endswith(".json") and is_pdf_file(p):
                files[p.resolve()] = p.stat().st_mtime
    return sorted(files, key=lambda p: (files[p], str(p)))


def resolve_cert_number(cert: CertificateData, path: Path) -> str | None:
    """The cert number printed on the PDF, else a sane filename prefix."""
    if cert.cert_number and _CERT_NO_RE.match(cert.cert_number):
        return cert.cert_number
    raw = cert.raw_data or {}
    if not raw.get("is_certificate"):
        return None
    fn = parse_filename_info(path.name).get("cert_number")
    if fn and _CERT_NO_RE.match(fn):
        return fn
    return None


def cert_columns(cert: CertificateData, cert_number: str, pdf_path: Path) -> dict:
    values = {"cert_number": cert_number, "pdf_path": str(pdf_path)}
    for f in _CERT_FIELDS:
        values[_FIELD_TO_COLUMN.get(f, f)] = getattr(cert, f)
    return values


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def find_boat_for_cert(conn, cert_number: str, sail_number: str | None, boat_name: str | None) -> int | None:
    """Pick the boat a certificate belongs to.

    1. ``boats.cert_number`` (the TCC listing's cert number) — when several
       boat rows share it, prefer the one most recently on the TCC listing,
       then a matching sail number.
    2. exact normalised sail number **and** boat name (sail numbers alone are
       reused across boats, so never match on them by themselves).
    """
    row = conn.execute(
        text(
            """
            SELECT b.id
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT max(snapshot_date) AS last_seen FROM tcc_snapshots t WHERE t.boat_id = b.id
            ) t ON true
            WHERE b.cert_number = :c
            ORDER BY t.last_seen DESC NULLS LAST,
                     (regexp_replace(upper(b.sail_number), '[^A-Z0-9]', '', 'g') = :s) DESC,
                     b.id
            LIMIT 1
            """
        ),
        {"c": cert_number, "s": _norm(sail_number)},
    ).first()
    if row:
        return int(row[0])
    if sail_number and boat_name:
        row = conn.execute(
            text(
                """
                SELECT b.id FROM boats b
                LEFT JOIN LATERAL (
                    SELECT max(snapshot_date) AS last_seen FROM tcc_snapshots t WHERE t.boat_id = b.id
                ) t ON true
                WHERE regexp_replace(upper(b.sail_number), '[^A-Z0-9]', '', 'g') = :s
                  AND regexp_replace(upper(b.boat_name), '[^A-Z0-9]', '', 'g') = :n
                ORDER BY t.last_seen DESC NULLS LAST, b.id
                LIMIT 1
                """
            ),
            {"s": _norm(sail_number), "n": _norm(re.sub(r"\s*-\s*(SEC|SER)\b.*$", "", boat_name))},
        ).first()
        if row:
            return int(row[0])
    return None


def link_unlinked_certificates(engine: Engine) -> int:
    """Attach ``boat_id`` to certificates that were parsed before their boat
    existed (the TCC import creates boats after the cert parse).  Only fills
    NULLs; existing links are never changed."""
    linked = 0
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, cert_number, raw_data->>'sail_number', raw_data->>'name' "
                "FROM irc_certificates WHERE boat_id IS NULL"
            )
        ).all()
    for cert_id, cert_number, sail, name in rows:
        with engine.begin() as conn:
            boat_id = find_boat_for_cert(conn, cert_number, sail, name)
            if boat_id is None:
                continue
            res = conn.execute(
                text("UPDATE irc_certificates SET boat_id = :b WHERE id = :i AND boat_id IS NULL"),
                {"b": boat_id, "i": cert_id},
            )
            linked += res.rowcount or 0
    return linked


def _existing_rows(engine: Engine) -> dict[str, datetime]:
    with engine.connect() as conn:
        return {
            r[0]: r[1]
            for r in conn.execute(
                text("SELECT cert_number, scraped_at FROM irc_certificates WHERE cert_number IS NOT NULL")
            )
        }


def ingest_certificates(
    engine: Engine,
    paths: Iterable[Path],
    *,
    incremental: bool = True,
    log: Callable[[str], None] = print,
    upsert=None,
) -> IngestStats:
    """Parse ``paths`` and upsert each certificate.

    ``incremental`` skips a file when its filename cert number already has a
    row scraped after the file was last modified — re-downloads (new mtime)
    and new files are always parsed.  One failing PDF or row never aborts
    the run.
    """
    from irc_data.db.operations import upsert_certificate_status

    upsert = upsert or upsert_certificate_status
    stats = IngestStats()
    existing = _existing_rows(engine) if incremental else {}

    for path in paths:
        path = Path(path)
        stats.files_seen += 1
        if incremental:
            fn_cert = parse_filename_info(path.name).get("cert_number")
            seen_at = existing.get(fn_cert) if fn_cert else None
            if seen_at is not None:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if seen_at >= mtime:
                    stats.skipped_unchanged += 1
                    continue
        try:
            cert = parse_certificate_pdf(path)
        except NotACertificate:
            stats.not_certificate += 1
            continue
        except Exception as exc:  # corrupt / non-PDF
            stats.parse_errors += 1
            stats.errors.append(f"parse {path.name}: {exc}"[:200])
            continue
        stats.parsed += 1
        cert_number = resolve_cert_number(cert, path)
        if not cert_number:
            stats.not_certificate += 1
            continue
        raw = cert.raw_data or {}
        try:
            with engine.connect() as conn:
                boat_id = find_boat_for_cert(conn, cert_number, raw.get("sail_number"), raw.get("name"))
            if boat_id:
                stats.matched += 1
            _id, status = upsert(engine, boat_id, cert_columns(cert, cert_number, path))
        except Exception as exc:
            stats.db_errors += 1
            stats.errors.append(f"db {path.name}: {exc}"[:200])
            log(f"  DB error for {path.name}: {str(exc)[:160]}")
            continue
        if status == "inserted":
            stats.inserted += 1
        elif status == "updated":
            stats.updated += 1
        else:
            stats.kept_newer += 1
        if stats.parsed % 250 == 0:
            log(f"  ... {stats.parsed} parsed ({stats.inserted} new, {stats.updated} updated)")

    stats.linked_after = link_unlinked_certificates(engine)
    return stats


def default_certificate_paths(include_historical: bool = False) -> list[Path]:
    """Every certificate PDF the platform has downloaded."""
    from irc_data.config import CERTIFICATES_DIR, HISTORICAL_CERTS_DIR, RAW_DIR

    dirs = [CERTIFICATES_DIR]
    if include_historical:
        dirs.append(HISTORICAL_CERTS_DIR)
    raw_store = RAW_DIR / "irc_pdfs"
    return iter_certificate_files(dirs, raw_store_dirs=[raw_store] if raw_store.exists() else [])

