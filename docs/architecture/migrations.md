# Canonical Database Migrations (DP-03-05)

> How the schema evolves without losing history or breaking consumers, and how
> the migration machinery is verified.

## Why this exists

The migration graph had grown into a tangle: duplicate revision ids
(`0023`×3, `0024`×2, `0025`×4) and **four alembic heads**, so
`alembic upgrade head` was ambiguous — a fresh database could not be built
deterministically, and the live dev database was stuck stamped with two
version rows (`0024`, `0025`).  DP-03-05 makes the chain **canonical**: a
single linear history from `0001` to a single head.

## The canonical chain

`alembic history` is now a single line.  The previously-duplicated tail was
renumbered onto unique, date-suffixed ids (no id is a prefix of another, which
alembic requires for unambiguous prefix resolution):

```
… 0022 → aa0f8e0c178b
      → 0023        (data_sources register)
      → 20260830a   (data_sources policy cols + source_incidents + domain_disables + seed)
      → 20260830b   (source monitor: baselines, health events, quarantine)
      → 0024        (raw_objects, retrieval_events)
      → 20260901a   (raw_lake_artifacts)
      → 20260901b   (replay_batches, replay_artifacts, publication_receipts)
      → 0025        (fact_assertions — bitemporal store)
      → 20260902a   (source_runs, source_schedule_state)
      → 20260902b   (watchdog_alerts)
      → 20260526a   (crawl_budget_settings, crawl_throttle_events)
      → 0026        (HEAD: compatibility views + migration/backup evidence)
```

Revisions that needed to tolerate databases that had already applied a sibling
branch were made **idempotent** (`CREATE TABLE IF NOT EXISTS`,
`ADD COLUMN IF NOT EXISTS`, `ON CONFLICT DO NOTHING`), so a database that took
any historical branch converges onto the same final schema.

## Compatibility views (the versioned consumer contract)

`0026` creates three **stable `v1_*` views**.  Consumers read through these;
as underlying tables evolve, the views are kept stable (or a `v2_*` is added
alongside) so existing reads keep working:

| View | Contract |
|------|----------|
| `v1_boat_ratings` | one row per boat with its latest TCC snapshot |
| `v1_race_results` | race results flattened via the strict-3NF join (`race_results → event_entries → events/boats`) |
| `v1_fact_assertions_current` | the current resolved truth from the bitemporal store (`status='active' AND superseded_by IS NULL`) |

## Migration evidence & backup checks

`0026` also creates two bookkeeping tables:

* **`schema_migrations`** — one row per applied revision (revision, applied_at,
  duration_ms, rows affected, notes).  The verification harness writes here.
* **`backup_checks`** — one row per backup/restore verification (backup_id,
  db_name, size_bytes, sha256, verified_at, status).  This records the backup
  checks required before/after a migration and documents the restore strategy.

## Rollback / restore strategy

`0026` is deliberately **additive** — it creates views + two bookkeeping
tables and alters nothing else.  Its `downgrade()` drops only those objects,
leaving all user data intact; re-running `upgrade()` recreates them.  That
downgrade→upgrade pair is the **tested rollback/restore path** (see
`tests/migrations/test_rollback.py`).

For a destructive migration in future, the strategy is: take a backup
(`pg_dump`), record it in `backup_checks`, apply the migration, verify
counts/hashes, and on failure restore from the recorded backup.

## Converging a legacy (multi-head) database

A database stamped with several `alembic_version` rows cannot `upgrade head`
("Requested revision … overlaps …").  Repair it once with:

```bash
PYTHONPATH=src python3 scripts/converge_legacy_heads.py \
    postgresql+psycopg://irc:irc@localhost:5433/irc_data
PYTHONPATH=src python3 -m alembic upgrade head
```

`converge_legacy_heads.py` never touches user data — it collapses the version
table to the point on the canonical chain the schema actually corresponds to
(refusing, with a clear message, if expected tables are missing), after which
`alembic upgrade head` walks the remaining idempotent steps normally.

## Verification (CI gate + evidence)

The compatibility suite lives in `api/tests/migrations/` and needs a reachable
PostgreSQL (it provisions throwaway databases; it skips cleanly when no DB is
available, or fails hard if `DP03_SKIP_IF_NO_DB=0`):

```bash
cd api
PYTHONPATH=src python3 -m pytest tests/migrations/ -v
```

It asserts: a single canonical head; no duplicate/prefix revision ids; a
linear chain; upgrade **from the previous supported schema** over a
production-sized synthetic dataset with **counts, content-hashes and consumer
queries** validated; the risky 0022 3NF backfill links every race_result;
migration completes **within the time budget**; and the rollback/restore pair
is data-preserving.

Human-runnable evidence generator (prints a paste-able PASS/FAIL log):

```bash
PYTHONPATH=src python3 scripts/verify_dp_03_05.py
```

Tunable via env: `DP03_MIGRATION_BUDGET_SECONDS` (default 120),
`DP03_N_BOATS` / `DP03_N_SNAPSHOTS` / `DP03_N_EVENTS` / `DP03_N_ENTRIES` /
`DP03_N_RESULTS` / `DP03_N_ASSERTIONS` (production-scale defaults), and
`DP03_ADMIN_DATABASE_URL` for the maintenance connection.
