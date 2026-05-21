"""Diagnostic CLIs that read from the shared DB without mutating it.

Currently:
  - scraper_parity: side-by-side legacy vs Firecrawl row counts per source,
    used during the 14-day parallel-run window before retiring a bespoke
    scraper.
"""
