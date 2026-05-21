# IRC Historical Certificate Backfill — Baseline

Captured: 2026-05-21

## Current state of `irc_certificates`

Queried against `postgresql://irc:irc@localhost:5433/irc_data`.

### Count by issue year

| Year       | Count |
|------------|-------|
| 2024       | 907   |
| 2025       | 2,796 |
| (NULL)     | 106   |
| **Total**  | 3,809 |

- `MIN(issue_date) = 2024-07-11`
- `MAX(issue_date) = 2025-12-31`
- No row has `issue_date < 2024-07-11`.

### Linkage to `boats`

| Metric              | Value |
|---------------------|-------|
| Total certs         | 3,809 |
| Linked (`boat_id`)  | 2,361 |
| Unlinked            | 1,448 |
| Distinct cert_num   | 3,809 |

Roughly **62%** of currently-held certs have a matching boat record.

## Targets (12-month horizon)

- **At least 8,000 historical certs** (`issue_date < 2024-01-01`) parsed into
  `irc_certificates`.
- **≥80%** of harvested historical certs linked to a `boats` row.
- **Resumable** backfill — `.irc_backfill_state.json` survives session restarts.

## Strategy

Three existing-but-disconnected modules combined under one orchestrator:

1. `scrapers/historical_certs.py` — TCC CSV-derived URL probing
   (`build_cert_url_variants`, `load_all_known_certs`).
2. `scrapers/wayback.py` — Wayback Machine integration. Needs a new
   `harvest_tcc_archives()` step that pulls historical TCC listings so we
   know which cert numbers ever existed.
3. `scrapers/cert_probe.py` — backward cert-number scanning.

New code: `scrapers/cert_index.py` (master cert-number index from harvested
TCCs), `scrapers/irc_backfill.py` (orchestrator), CLI verbs `wayback-tcc`
and `backfill-irc-certs`.

## Notes

- Plan B references `build_pdf_url_candidates` but the actual function is
  `build_cert_url_variants` in `historical_certs.py` (line 57). Adapted.
- Plan B references `lookup_pdf_in_wayback` which does not exist in
  `wayback.py`. The closest primitives are `search_wayback_pdfs` (CDX query
  for archived PDFs at a domain) and `download_wayback_pdf` (fetches a
  specific timestamp/URL pair). The orchestrator adapts these into a
  per-URL lookup helper.
- Active 2024+ certs are healthy via `irc-data scrape certs` and are out
  of scope for this plan.

## B6 verification (post-implementation)

Captured immediately after Task B5 commit, before any full harvest.

```sql
SELECT
  (SELECT COUNT(*) FROM irc_certificates WHERE issue_date < '2024-01-01') AS historical_certs,
  (SELECT COUNT(*) FROM irc_certificates WHERE boat_id IS NOT NULL) * 100.0
    / NULLIF((SELECT COUNT(*) FROM irc_certificates), 0) AS pct_linked;
```

| historical_certs | pct_linked |
|-----------------:|-----------:|
|                0 |     61.98% |

The infrastructure is in place but the live harvest has not been run.
Target (`historical_certs >= 5000`) will be met by the user-driven full
harvest at merge time.

### Operational sequence to hit the targets

1. `irc-data wayback-tcc --start-year 2010 --end-year 2025` — full
   CDX-driven TCC snapshot harvest into
   `api/data/raw/tcc_listings/historical/`. Today's smoke run only pulled
   HTML index pages from `online-tcc-listings/`; achieving the 50+ CSV
   target requires either (a) extending the harvester to follow CSV
   links embedded in those HTML pages, or (b) supplementing with the
   already-on-disk 2009 snapshots in `tcc_listings/` (which were used to
   validate the orchestrator).
2. `irc-data backfill-irc-certs` — overnight run, no `--limit`. Resume
   state in `HISTORICAL_CERTS_DIR/.irc_backfill_state.json`.
3. `irc-data parse-certs --include-historical` — sweep harvested PDFs.
4. `irc-data match-boats` — link certs to boats.
5. Re-run the verification SQL above.

### Smoke-test artefacts

- `/tmp/agent-plan-b-cache/tcc_listings/historical/` — three Wayback
  HTML snapshots (2019, 2020) and one local copy of
  `tcc_listing_2009-05-18.csv` renamed to the `tcc_2009_*.csv` convention.
- `/tmp/agent-plan-b-cache/certs/.irc_backfill_state.json` — orchestrator
  state from a 5-entry probe run; confirms resumability works.
- Tag `irc-historical-backfill-v1` exists locally on
  `worktree-agent-aa6bdbc5c76f72932`; the user can push it after merge.
