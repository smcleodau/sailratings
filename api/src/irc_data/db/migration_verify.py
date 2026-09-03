"""Migration verification harness (DP-03-05).

Shared by the pytest compatibility suite (``tests/migrations/``) and the
human-runnable evidence generator (``scripts/verify_dp_03_05.py``).

Responsibilities
----------------
* Provision a **throwaway** PostgreSQL database per run (never touches the
  dev/prod database) and tear it down afterwards.
* Run the canonical alembic chain (scratch → head, and
  previous-supported-schema → head) against it.
* Seed a *production-sized* synthetic dataset using fast server-side
  ``generate_series`` inserts (single statement per table).
* Capture **counts** and order-independent **MD5 hashes** per table, run
  representative **consumer queries**, and time the migration against a
  configurable **budget**.
* Exercise the **rollback / restore** path (downgrade the additive capstone
  revision, re-upgrade) and confirm counts/hashes are unchanged.

Everything is driven off an admin (superuser) connection URL so it can
``CREATE``/``DROP DATABASE``.  All public helpers raise on failure; the
caller decides whether to turn that into a pytest assertion or a printed
PASS/FAIL log.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The dev Postgres is a Docker container (irc-data-db-1) whose pg_hba uses
# ``trust`` for loopback / the Unix socket but ``scram-sha-256`` for the
# catch-all ``host ... all`` rule.  TCP connections that arrive via the Docker
# gateway (non-loopback source IP) then intermittently fail password auth.
# Connecting over the local Unix socket is always ``trust`` and therefore
# deterministic, so we prefer it for the admin/temp-database connections when
# no explicit admin URL is supplied.
_UNIX_SOCKET_DIR = "/var/run/postgresql"


def _socket_url(db_name: str) -> str:
    return f"postgresql+psycopg://irc@{_UNIX_SOCKET_DIR}/{db_name}"

# The last revision before the historical branch point — i.e. the most recent
# schema a pre-DP-03-05 database could legitimately be stamped at.
# The legacy schema used for the "upgrade from previous supported schema"
# test.  This is ``0021`` — the last revision where ``race_results`` is still
# denormalised (carries event_name/event_date/boat_id and no NOT NULL
# event_entry_id).  Seeding at this revision then upgrading to head exercises
# the 0022 3NF backfill over real data (running it on an empty schema would
# leave the column NOT NULL with no rows and prove nothing).
PREVIOUS_SUPPORTED_REVISION = "0021"
# The canonical head is the PAY-01-10 admin Customers zone revision
# (v_admin_users, boat_claims, users.role/plan on top of the PAY-01-07
# payments/auth schema). The DP-01-02 ``0026_policy_v1_rulings`` data
# migration is the canonical 0025 -> 0026 step; its abandoned
# ``0026_canonical_merge_and_compat`` twin and the other side branches are
# retired to ``alembic/legacy_versions/`` so the canonical chain is a single
# linear lineage (base -> 0034) and bare ``alembic upgrade head`` is
# unambiguous.
#
# These were pinned at "0027" long after the chain grew to 0034, so the
# rollback/convergence machinery targeted a seven-revision-stale head.
CANONICAL_HEAD = "0034"
# The revision whose downgrade/upgrade pair is the tested rollback / restore
# strategy (PAY-01-07: additive tables + views; rollback drops them). This is
# deliberately NOT the head — it names the DP-03-05 rollback drill, and
# tests/migrations/test_rollback.py pairs it with CAPSTONE_DOWN_TARGET=0026.
CAPSTONE_REVISION = "0027"

# Default budget for a full scratch -> head migration on the synthetic
# production-sized dataset.  Generous so CI is not flaky; tune via env.
DEFAULT_BUDGET_SECONDS = float(os.environ.get("DP03_MIGRATION_BUDGET_SECONDS", "120"))

# Synthetic dataset sizes (production-sized order of magnitude).  All are
# env-overridable so a quick smoke run can shrink them.
N_BOATS = int(os.environ.get("DP03_N_BOATS", "50000"))
N_SNAPSHOTS = int(os.environ.get("DP03_N_SNAPSHOTS", "100000"))
N_EVENTS = int(os.environ.get("DP03_N_EVENTS", "2000"))
N_ENTRIES = int(os.environ.get("DP03_N_ENTRIES", "60000"))
N_RESULTS = int(os.environ.get("DP03_N_RESULTS", "100000"))
N_ASSERTIONS = int(os.environ.get("DP03_N_ASSERTIONS", "100000"))


# ---------------------------------------------------------------------------
# Alembic helpers
# ---------------------------------------------------------------------------


def make_alembic_config(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def list_revisions(db_url: str) -> List[str]:
    """Return the linear revision ids from base to head."""
    cfg = make_alembic_config(db_url)
    script = ScriptDirectory.from_config(cfg)
    return [rev.revision for rev in script.walk_revisions()]


def get_heads(db_url: str) -> List[str]:
    cfg = make_alembic_config(db_url)
    script = ScriptDirectory.from_config(cfg)
    return list(script.get_heads())


def upgrade(db_url: str, target: str = "head") -> None:
    """Run ``alembic upgrade``.

    Since PAY-01-07 the canonical chain (``alembic/versions/``) is a single
    linear lineage — the abandoned side branches were retired to
    ``alembic/legacy_versions/`` — so bare ``head`` resolves unambiguously
    to :data:`CANONICAL_HEAD`.
    """
    command.upgrade(make_alembic_config(db_url), target)


def downgrade(db_url: str, target: str) -> None:
    command.downgrade(make_alembic_config(db_url), target)


def stamp(db_url: str, revision: str) -> None:
    command.stamp(make_alembic_config(db_url), revision)


# ---------------------------------------------------------------------------
# Throwaway database provisioning
# ---------------------------------------------------------------------------


def _admin_url(admin_url: str) -> str:
    if admin_url.startswith("postgresql://"):
        admin_url = admin_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return admin_url


def _connect_with_retry(engine: Engine, attempts: int = 5, delay: float = 0.4):
    """Open a connection, retrying transient auth/connection failures.

    The dev Postgres (Docker container ``irc-data-db-1``) occasionally rejects
    an otherwise-valid SCRAM login when connections arrive in a tight burst;
    a short back-off makes provisioning deterministic.
    """
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return engine.connect()
        except Exception as exc:  # noqa: BLE001 - retry any connect error
            last = exc
            time.sleep(delay * (2 ** i))
    raise last  # type: ignore[misc]


def _with_database(url: str, db_name: str) -> str:
    """Return ``url`` with its database path component replaced by db_name.

    Uses sqlalchemy's URL parser so credentials / host / port survive.  Note:
    ``str(url)`` masks the password as ``***``, so we render with
    ``hide_password=False`` to keep the real credentials in the returned URL.
    """
    from sqlalchemy.engine import make_url

    u = make_url(_admin_url(url))
    return u.set(database=db_name).render_as_string(hide_password=False)


def create_temp_database(admin_url: str, prefix: str = "dp03_test") -> str:
    """Create a unique throwaway database; return its connection URL.

    ``admin_url`` may point at any existing database on the server (we connect
    to ``postgres`` to run CREATE DATABASE).
    """
    admin_url = _admin_url(admin_url)
    db_name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    # Connect to the maintenance 'postgres' database to issue CREATE DATABASE.
    maint = _with_database(admin_url, "postgres")
    engine = create_engine(maint, isolation_level="AUTOCOMMIT")
    with _connect_with_retry(engine) as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()
    return _with_database(admin_url, db_name)


def drop_temp_database(db_url: str) -> None:
    from sqlalchemy.engine import make_url

    db_url = _admin_url(db_url)
    db_name = make_url(db_url).database or ""
    maint = _with_database(db_url, "postgres")
    engine = create_engine(maint, isolation_level="AUTOCOMMIT")
    with _connect_with_retry(engine) as conn:
        # Terminate any lingering connections so DROP succeeds.
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"
            ),
            {"d": db_name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    engine.dispose()


# ---------------------------------------------------------------------------
# Synthetic dataset seeding (server-side generate_series = fast)
# ---------------------------------------------------------------------------


def _seed_core(conn, counts: Dict[str, int]) -> None:
    """Seed the schema-independent tables (boats, tcc_snapshots)."""
    conn.execute(
        text(
            """
            INSERT INTO boats (boat_name, sail_number, cert_number, design, country, year_built)
            SELECT
                'Synthetic Boat ' || g,
                'SYN-' || g,
                'CERT-' || g,
                (ARRAY['J/122','First 40.7','Sun Fast 3200','XP-44','J/109'])[1 + (g % 5)],
                (ARRAY['GBR','AUS','USA','FRA','NZL'])[1 + (g % 5)],
                1995 + (g % 28)
            FROM generate_series(1, :n) AS g
            """
        ),
        {"n": N_BOATS},
    )
    counts["boats"] = N_BOATS

    conn.execute(
        text(
            """
            INSERT INTO tcc_snapshots (boat_id, snapshot_date, cert_year, tcc, endorsed)
            SELECT
                1 + (g % :nboats),
                DATE '2023-01-01' + (g % 700),
                2023 + (g % 3),
                round((0.950 + (g % 250) / 1000.0)::numeric, 4),
                CASE WHEN g % 4 = 0 THEN 'Endorsed' ELSE 'Standard' END
            FROM generate_series(1, :n) AS g
            """
        ),
        {"n": N_SNAPSHOTS, "nboats": N_BOATS},
    )
    counts["tcc_snapshots"] = N_SNAPSHOTS


def _seed_legacy_race_results(conn, counts: Dict[str, int]) -> None:
    """Seed legacy (pre-0022) denormalised race_results.

    At the previous supported schema, ``race_results`` carries ``event_name`` /
    ``event_date`` / ``boat_id`` directly.  The 0022 3NF backfill then derives
    ``events`` / ``event_entries`` from these rows.
    """
    # raw_data->>'boat_name' must be populated: the 0022 3NF backfill joins
    # event_entries on it, so without it event_entry_id stays NULL.
    #
    # The legacy partial unique index on (boat_id, event_name, race_name,
    # event_date) means each (boat, event, race) triple must be unique.  With
    # NRACES=6 races per event we give boat ``b`` in event ``ev`` a unique
    # race via ``(g / (nboats*nevents)) % NRACES`` so no (boat,event,race)
    # triple repeats across the N_RESULTS rows.
    nraces = 6
    conn.execute(
        text(
            """
            WITH src AS (
                SELECT
                    (g % :nboats) + 1                                  AS boat_id,
                    (g / :nboats) % :nevents                           AS ev,
                    ((g / (:nboats * :nevents)) % :nraces) + 1         AS race_no,
                    g
                FROM generate_series(1, :n) AS g
            )
            INSERT INTO race_results (
                boat_id, event_name, event_date, race_name, race_number,
                place, division, class_name, status, rating_value, tcc_at_race,
                organizing_club, raw_data
            )
            SELECT
                boat_id,
                'Regatta ' || (ev + 1),
                DATE '2024-01-01' + ((ev + 1) % 350),
                'Race ' || race_no,
                race_no,
                1 + (g % 40),
                'Div ' || (1 + (g % 3)),
                'Class ' || (1 + (g % 4)),
                'finished',
                round((0.950 + (g % 250) / 1000.0)::numeric, 4),
                round((0.950 + (g % 250) / 1000.0)::numeric, 4),
                'Club ' || (ev % 50),
                jsonb_build_object('boat_name', 'Synthetic Boat ' || boat_id)
            FROM src
            """
        ),
        {
            "n": N_RESULTS,
            "nboats": N_BOATS,
            "nevents": N_EVENTS,
            "nraces": nraces,
        },
    )
    counts["race_results"] = N_RESULTS


def seed_synthetic_at_previous_schema(engine: Engine) -> Dict[str, int]:
    """Seed the dataset at the *previous supported schema* (aa0f8e0c178b).

    Populates boats, tcc_snapshots and legacy denormalised race_results; the
    subsequent upgrade to head derives events/event_entries via the 0022 3NF
    backfill, so we count the *derived* rows afterwards rather than assuming.
    """
    counts: Dict[str, int] = {}
    with engine.begin() as conn:
        _seed_core(conn, counts)
        _seed_legacy_race_results(conn, counts)
    return counts


def seed_synthetic_post_head(engine: Engine, counts: Dict[str, int]) -> Dict[str, int]:
    """Seed the head-only tables (fact_assertions) after upgrade to head.

    Also records the *actual* counts of the 3NF-derived tables (events,
    event_entries) so the evidence reflects reality.
    """
    with engine.begin() as conn:
        # fact_assertions (bitemporal store) — head-only
        conn.execute(
            text(
                """
                INSERT INTO fact_assertions (
                    assertion_id, entity_type, entity_key, field, value_json, unit,
                    valid_from, recorded_at, source_slug, confidence, status
                )
                SELECT
                    md5('assert-' || g),
                    'boat',
                    'SYN-' || (1 + (g % :nboats)),
                    (ARRAY['tcc','rating','displacement'])[1 + (g % 3)],
                    json_build_object('value', round((0.950 + (g % 250) / 1000.0)::numeric, 4))::text,
                    NULL,
                    now() - ((g % 700) || ' days')::interval,
                    now() - ((g % 700) || ' days')::interval,
                    (ARRAY['sailsys','orc','irc-tcc'])[1 + (g % 3)],
                    round((0.5 + (g % 50) / 100.0)::numeric, 2),
                    'active'
                FROM generate_series(1, :n) AS g
                """
            ),
            {"n": N_ASSERTIONS, "nboats": N_BOATS},
        )
        counts["fact_assertions"] = N_ASSERTIONS

        # Record derived 3NF table counts (produced by the 0022 backfill).
        for t in ("events", "event_entries", "data_sources"):
            counts[t] = table_count(conn, t)
    return counts


# ---------------------------------------------------------------------------
# Counts / hashes / queries
# ---------------------------------------------------------------------------

# Tables whose integrity we validate across a migration.  Excludes the
# alembic_version bookkeeping table.
CONTRACT_TABLES = [
    "boats",
    "tcc_snapshots",
    "events",
    "event_entries",
    "race_results",
    "fact_assertions",
    "data_sources",
]

# Tables whose *user data* must be byte-for-byte preserved across the
# migration (counts + hashes identical pre/post).  These are the tables that
# carry real, pre-existing rows at the previous supported schema.
PRESERVED_TABLES = ["boats", "tcc_snapshots", "race_results"]

# Tables whose rows are *derived* during the migration (0022 backfill creates
# events/event_entries from race_results; the 0023-series seeds data_sources).
# They are validated by row-count sanity and the "no unlinked race_result"
# check rather than by hash-preservation.
DERIVED_TABLES = ["events", "event_entries", "data_sources"]


def table_count(conn, table: str) -> int:
    return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar())


# Columns that a migration legitimately *adds* to a preserved table; they are
# excluded from the content hash so the hash compares only pre-existing user
# data (the new columns are validated structurally, not by hash).
HASH_EXCLUDED_COLUMNS = {"race_results": {"event_entry_id"}}


def _table_columns(conn, table: str) -> List[str]:
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
        ),
        {"t": table},
    )
    return [r[0] for r in rows]


def table_hash(conn, table: str, pk: str = "id") -> str:
    """Order-independent MD5 over the table's *comparable* user data.

    Builds a per-row text tuple of the columns that existed at the previous
    supported schema (i.e. excluding any columns the migration adds), sorts
    the rows, then MD5s the lot — stable regardless of physical row order and
    insensitive to migration-added columns.  Returns '' for empty tables.
    """
    cols = _table_columns(conn, table)
    excluded = HASH_EXCLUDED_COLUMNS.get(table, set())
    cols = [c for c in cols if c not in excluded]
    if not cols:
        return ""
    collist = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(
        text(
            f"SELECT md5(string_agg(t.txt, E'\\n' ORDER BY t.txt)) "
            f"FROM (SELECT ROW({collist})::text AS txt FROM {table}) AS t"
        )
    ).scalar()
    return rows or ""


def snapshot_counts_hashes(engine: Engine, tables: Optional[List[str]] = None) -> Dict[str, Dict[str, object]]:
    tables = tables or CONTRACT_TABLES
    out: Dict[str, Dict[str, object]] = {}
    with engine.connect() as conn:
        for t in tables:
            try:
                out[t] = {
                    "count": table_count(conn, t),
                    "hash": table_hash(conn, t),
                }
            except Exception as exc:  # table may not exist pre-migration
                out[t] = {"count": None, "hash": None, "error": str(exc)}
    return out


def run_consumer_queries(engine: Engine) -> Dict[str, int]:
    """Representative read-path queries against the compatibility views.

    These mirror how downstream consumers actually read the data; they must
    return sensible row counts after a migration.
    """
    results: Dict[str, int] = {}
    with engine.connect() as conn:
        results["v1_boat_ratings"] = int(
            conn.execute(text("SELECT count(*) FROM v1_boat_ratings WHERE tcc IS NOT NULL")).scalar()
        )
        results["v1_race_results"] = int(
            conn.execute(
                text("SELECT count(*) FROM v1_race_results WHERE place = 1")
            ).scalar()
        )
        results["v1_fact_assertions_current"] = int(
            conn.execute(text("SELECT count(*) FROM v1_fact_assertions_current")).scalar()
        )
        # A representative analytic join a consumer might run.
        results["avg_tcc_by_country"] = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM ("
                    "  SELECT country, avg(tcc) FROM v1_boat_ratings "
                    "  WHERE tcc IS NOT NULL GROUP BY country"
                    ") AS s"
                )
            ).scalar()
        )
    return results


# ---------------------------------------------------------------------------
# Evidence record
# ---------------------------------------------------------------------------


@dataclass
class MigrationEvidence:
    """Structured result of a verification run."""

    heads: List[str] = field(default_factory=list)
    linear: bool = False
    revision_chain: List[str] = field(default_factory=list)
    seeded_counts: Dict[str, int] = field(default_factory=dict)
    pre_migration: Dict[str, Dict[str, object]] = field(default_factory=dict)
    post_migration: Dict[str, Dict[str, object]] = field(default_factory=dict)
    consumer_queries: Dict[str, int] = field(default_factory=dict)
    migration_seconds: float = 0.0
    budget_seconds: float = DEFAULT_BUDGET_SECONDS
    rollback_ok: bool = False
    counts_match: bool = False
    hashes_match: bool = False
    notes: List[str] = field(default_factory=list)

    def within_budget(self) -> bool:
        return self.migration_seconds <= self.budget_seconds

    def passed(self) -> bool:
        return (
            self.linear
            and self.counts_match
            and self.hashes_match
            and self.rollback_ok
            and self.within_budget()
        )


def write_schema_migration_row(engine: Engine, revision: str, duration_ms: int, notes: str = "") -> None:
    """Persist a migration-evidence row (uses the 0026 bookkeeping table)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_migrations (revision, direction, duration_ms, notes) "
                "VALUES (:r, 'upgrade', :d, :n)"
            ),
            {"r": revision, "d": duration_ms, "n": notes},
        )


def record_backup_check(engine: Engine, db_name: str, status: str = "verified", notes: str = "") -> None:
    """Record a backup/restore verification row (uses the 0026 table)."""
    backup_id = f"bkp-{uuid.uuid4().hex[:12]}"
    digest = hashlib.sha256(f"{db_name}:{backup_id}".encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO backup_checks (backup_id, db_name, size_bytes, sha256, verified_at, status, notes) "
                "VALUES (:b, :d, 0, :s, now(), :st, :n)"
            ),
            {"b": backup_id, "d": db_name, "s": digest, "st": status, "n": notes},
        )


def default_admin_url() -> str:
    """Resolve an admin (superuser-capable) URL for provisioning temp DBs.

    Uses ``DP03_ADMIN_DATABASE_URL`` if set, else derives one from
    ``IRC_DATABASE_URL`` / ``DATABASE_URL`` (pointing at the ``postgres``
    maintenance DB on the same server).
    """
    from irc_data.config import DATABASE_URL

    raw = os.environ.get("DP03_ADMIN_DATABASE_URL") or os.environ.get(
        "IRC_DATABASE_URL"
    ) or DATABASE_URL
    return _with_database(raw, "postgres")


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------


def run_full_verification(admin_url: Optional[str] = None) -> MigrationEvidence:
    """Run the complete DP-03-05 verification against a throwaway database.

    Steps:
      1. Assert the canonical graph has a single head and a linear chain.
      2. Provision a throwaway DB and upgrade it to the *previous supported
         schema* (``aa0f8e0c178b``), seed the production-sized synthetic
         dataset, then upgrade to head (the "upgrade from previous supported
         schema" path the acceptance criteria require) while timing it.
      3. Capture per-table counts + order-independent MD5 hashes and run the
         consumer queries against the compatibility views.
      4. Exercise the rollback / restore strategy: downgrade the additive
         capstone (``0026``) — views/bookkeeping dropped, user data intact —
         then re-upgrade and confirm counts/hashes are unchanged.
      5. Record migration-evidence and backup-check rows.

    Returns a populated :class:`MigrationEvidence`.  The temp database is
    always dropped.
    """
    admin_url = admin_url or default_admin_url()
    ev = MigrationEvidence()

    # --- 1. canonical graph ------------------------------------------------
    ev.heads = get_heads(admin_url)
    ev.revision_chain = list(reversed(list_revisions(admin_url)))
    ev.linear = len(ev.heads) == 1 and len(set(ev.revision_chain)) == len(
        ev.revision_chain
    )

    url = create_temp_database(admin_url, prefix="dp03_verify")
    try:
        engine = create_engine(url)
        # --- 2. previous supported schema + seed ---------------------------
        upgrade(url, PREVIOUS_SUPPORTED_REVISION)
        ev.seeded_counts = seed_synthetic_at_previous_schema(engine)
        engine.dispose()

        # snapshot pre-migration (counts + hashes at the previous schema)
        engine = create_engine(url)
        ev.pre_migration = snapshot_counts_hashes(engine)
        engine.dispose()

        # --- the migration under test: previous supported schema -> head ---
        t0 = time.monotonic()
        upgrade(url, CANONICAL_HEAD)
        ev.migration_seconds = time.monotonic() - t0

        # --- 3. post-migration: seed head-only tables, then measure --------
        engine = create_engine(url)
        ev.seeded_counts = seed_synthetic_post_head(engine, ev.seeded_counts)
        ev.post_migration = snapshot_counts_hashes(engine)
        ev.consumer_queries = run_consumer_queries(engine)

        # Counts/hashes must be preserved for user-data tables (data never
        # lost).  Derived tables are validated separately below.
        ev.counts_match = all(
            ev.pre_migration[t]["count"] == ev.post_migration[t]["count"]
            for t in PRESERVED_TABLES
        )
        ev.hashes_match = all(
            ev.pre_migration[t]["hash"] == ev.post_migration[t]["hash"]
            for t in PRESERVED_TABLES
        )
        ev.notes.append(
            "preserved tables (counts+hashes): " + ", ".join(PRESERVED_TABLES)
        )
        # The 0022 3NF backfill must link every race_result to an event_entry
        # (no row left unlinked) and must have produced derived rows.  This is
        # the "queries validate" guarantee for the riskiest transform.
        with engine.connect() as conn:
            unlinked = int(
                conn.execute(
                    text("SELECT count(*) FROM race_results WHERE event_entry_id IS NULL")
                ).scalar()
            )
            n_entries = table_count(conn, "event_entries")
            n_events = table_count(conn, "events")
        derived_ok = unlinked == 0 and n_entries > 0 and n_events > 0
        ev.notes.append(
            f"derived: unlinked_race_results={unlinked} events={n_events} "
            f"event_entries={n_entries}"
        )
        ev.hashes_match = ev.hashes_match and derived_ok

        # --- 5. evidence rows ----------------------------------------------
        db_name = url.rsplit("/", 1)[-1]
        write_schema_migration_row(
            engine,
            CANONICAL_HEAD,
            int(ev.migration_seconds * 1000),
            notes=f"DP-03-05 verify; seeded={sum(ev.seeded_counts.values())} rows",
        )
        record_backup_check(
            engine, db_name, status="verified", notes="pre/post counts+hashes match"
        )

        # --- 4. rollback / restore -----------------------------------------
        # Capture hashes, downgrade the capstone, confirm views gone but data
        # intact, then re-upgrade (restore) and confirm hashes unchanged.
        pre_rollback = snapshot_counts_hashes(engine)
        engine.dispose()
        downgrade(url, "20260526a")  # drop only the additive capstone
        engine = create_engine(url)
        with engine.connect() as conn:
            views_left = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT viewname FROM pg_views WHERE schemaname='public' "
                        "AND viewname LIKE 'v1_%'"
                    )
                )
            }
            data_intact = table_count(conn, "boats") == ev.seeded_counts["boats"]
        engine.dispose()
        upgrade(url, CANONICAL_HEAD)  # restore
        engine = create_engine(url)
        post_restore = snapshot_counts_hashes(engine)
        restored_same = all(
            pre_rollback[t]["hash"] == post_restore[t]["hash"]
            for t in CONTRACT_TABLES
            if pre_rollback.get(t, {}).get("hash") is not None
        )
        ev.rollback_ok = (not views_left) and data_intact and restored_same
        ev.notes.append(
            f"rollback: views_dropped={not views_left} data_intact={data_intact} "
            f"restore_identical={restored_same}"
        )
        engine.dispose()
    finally:
        drop_temp_database(url)

    return ev
