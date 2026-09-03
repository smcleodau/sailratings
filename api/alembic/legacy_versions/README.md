# legacy_versions — retired alembic side branches

These migration files were **removed from the canonical chain** as part of
PAY-01-07 so that `alembic/versions/` is a single linear lineage with unique
revision ids (base → … → 0025 → 0026 → 0027) and bare `alembic upgrade head`
is unambiguous again.

They are kept here (out of the alembic search path) for **archaeology only**
— they are never executed.  Their objects either already exist in the dev
database (stamped from a retired branch) or are re-applied idempotently by
the canonical chain (`0027_payments_auth` carries the DP-03-05
`schema_migrations` / `backup_checks` evidence tables and the stable
`v1_boat_ratings` / `v1_race_results` / `v1_fact_assertions_current` views).

## Converging a database stamped on a retired branch

Databases stamped at a revision that no longer exists in `versions/` (e.g.
`0029`/`0030`/`0031`, or the duplicated `0026`) must be re-stamped onto the
canonical ancestor whose objects they actually contain, then upgraded:

```bash
# example: dev was stamped 0030 (retired branch) but only contains the
# canonical objects -> converge onto canonical 0025, then upgrade.
psql "$DATABASE_URL" -c "UPDATE alembic_version SET version_num='0025';"
alembic upgrade head
```

Files retired here:

| file | reason |
|------|--------|
| `0024b_raw_lake_metadata_index.py`, `0024c_replay_backfill.py` | abandoned raw-lake side branches (duplicate chain off `0024`) |
| `0025b_schedule_registry.py`, `0025c_watchdog_alerts.py` | abandoned schedule-registry side branches (`watchdog_alerts` is runtime-ensured by `irc_data.scrape_watchdog.ensure_watchdog_table`) |
| `0025d_crawl_budget.py` | abandoned crawl-budget side branch |
| `0026_canonical_merge_and_compat.py` | abandoned duplicate-id `0026`; its compat surface is folded into `0027_payments_auth` |
| `0027_data_sources_notion_tiers.py` | duplicate-id `0027`, not yet applied anywhere |
| `0027_scheduling_policy_fields.py` … `0031_crawl_daily_cap.py` | abandoned 2026090x chain off the retired `0025b`–`0025d` branch |
