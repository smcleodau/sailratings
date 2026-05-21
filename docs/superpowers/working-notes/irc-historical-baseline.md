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
