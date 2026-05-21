"""Diagnostic queries and CLIs that read from the shared DB without mutating it.

Read-only helpers that summarise the state of the ingestion pipeline:

  - ``scraper_parity``: side-by-side legacy vs Firecrawl row counts per source,
    used during the 14-day parallel-run window before retiring a bespoke
    scraper.
  - ``orc_reports``: which ORC certs failed to link to an IRC boat, how much
    detail-coverage the backfill has achieved.

Invoked via ``irc-data report …`` and ``irc-data parity-report`` CLI verbs.
"""
