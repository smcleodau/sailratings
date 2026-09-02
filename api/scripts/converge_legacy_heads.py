#!/usr/bin/env python3
"""One-off convergence for databases stamped with legacy multi-head versions (DP-03-05).

Background
----------
Before the canonical chain, a database could carry **multiple**
``alembic_version`` rows (e.g. ``0024`` *and* ``0025``) because the migration
graph had several heads and duplicate revision ids.  Alembic cannot plan an
``upgrade head`` from such a state — it resolves the two rows as two distinct
ancestors of the head and aborts with::

    Requested revision 0025 overlaps with other requested revisions 0024

This script repairs that state **without touching user data**:

  1. Reads the current ``alembic_version`` rows.
  2. If there is a single row already on the canonical chain, does nothing.
  3. If there are multiple / legacy rows, it verifies the schema actually
     matches the canonical head's expectations (the DP tables that the legacy
     branches created are present) and then collapses the version table to the
     point on the canonical chain the schema corresponds to, so a subsequent
     ``alembic upgrade head`` walks the remaining steps normally.

The script is deliberately conservative: it *never* drops or alters user
tables, and it refuses to stamp a revision whose expected tables are missing
(printing what to run instead).

Usage::

    PYTHONPATH=src python3 scripts/converge_legacy_heads.py \\
        postgresql+psycopg://irc:irc@localhost:5433/irc_data

or with ``DATABASE_URL``/``IRC_DATABASE_URL`` set::

    PYTHONPATH=src python3 scripts/converge_legacy_heads.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from irc_data.db import migration_verify as mv  # noqa: E402

# Map legacy version rows -> the tables that row's branch is expected to have
# created.  Used to sanity-check the schema before collapsing versions.
_LEGACY_TABLE_EXPECTATIONS = {
    "0023": {"data_sources"},
    "0024": {"raw_objects", "retrieval_events"},  # raw_objects branch
    "0025": {"fact_assertions"},                  # fact_assertions branch
}


def _current_versions(engine) -> list[str]:
    with engine.connect() as conn:
        try:
            return [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        except Exception:
            return []


def _existing_tables(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
        }


def converge(db_url: str) -> int:
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(db_url)
    versions = _current_versions(engine)
    tables = _existing_tables(engine)
    print(f"current alembic_version rows: {versions}")
    print(f"detected {len(tables)} public tables")

    canonical_ids = set(mv.list_revisions(db_url))
    # Already canonical single-head?
    if len(versions) == 1 and versions[0] in canonical_ids:
        print(f"already on canonical chain at {versions[0]!r}; nothing to do.")
        print("Run:  alembic upgrade head")
        return 0

    if not versions:
        print("no alembic_version rows — database is unmanaged; run:")
        print("  irc-data db-upgrade   (or)   alembic upgrade head")
        return 0

    # Multiple/legacy rows: collapse to the newest row that is still a valid
    # canonical revision, after confirming its expected tables exist.
    legacy = [v for v in versions if v in _LEGACY_TABLE_EXPECTATIONS]
    target = None
    for cand in ("0025", "0024", "0023"):  # newest-first
        if cand in versions:
            expected = set().union(*(_LEGACY_TABLE_EXPECTATIONS[v] for v in versions if v in _LEGACY_TABLE_EXPECTATIONS))
            missing = {t for t in expected if t not in tables}
            if missing:
                print(f"REFUSE: schema is missing expected tables {missing} for legacy rows {legacy}")
                print("The database schema does not match its recorded versions;")
                print("restore from backup or rebuild before converging.")
                return 2
            target = cand
            break

    if target is None:
        print(f"no recognised legacy rows among {versions}; manual review needed.")
        return 2

    print(f"collapsing version table {versions} -> single row {target!r}")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": target})
    engine.dispose()
    print(f"version table now: {target!r}")
    print("Next: run  alembic upgrade head  to walk the remaining canonical steps.")
    return 0


if __name__ == "__main__":
    import os

    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("IRC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    if not url:
        print("ERROR: provide a database URL (arg or IRC_DATABASE_URL/DATABASE_URL)")
        raise SystemExit(2)
    raise SystemExit(converge(url))
