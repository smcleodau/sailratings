# Firecrawl Parity Baseline — 2026-05-21

First live-test of the Firecrawl + Claude extractor pipeline after merging
Plans A/B/C and adding the confidence gate. The goal is to know which
long-tail sources are *ready* to be Firecrawl-only and which still need
work before retiring the legacy scrapers.

## Setup state

- Migration 0019 applied — `race_results.transport` column exists.
- All 255,784 pre-existing rows backfilled to `transport='legacy'`.
- `upsert_race_result` defaults `transport='legacy'` when callers don't set
  it (bespoke scrapers + JSON imports inherit this).
- Firecrawl callers explicitly pass `transport='firecrawl'` via
  `import_scraper_results`.
- Confidence floor `CONFIDENCE_FLOOR = 0.70` in
  `api/src/irc_data/discovery/extractor.py`. Extractions below floor are
  routed to `ingest_events.status='quarantined'`; nothing is written to
  `race_results`.
- Cron still held (per operator decision).

## Per-source baseline (live extractions, 2026-05-21)

| Source | Sample URL | Format | Confidence | Outcome | Notes |
|---|---|---|---|---|---|
| RHKYC | ATIR2024-ATI_Overall.pdf | PDF | 0.35 | **Quarantined** | OCR garbage: "SEAWOLF"→"Sawedow", "Phoenix"→"Phonex". Legacy scraper has hand-tuned PDF parsing; Firecrawl markdown of this PDF is unreliable. |
| ISORA | results-2012?id=96 | HTML (Sailwave) | 0.95 | Ready | 18 rows, names and TCCs look correct (Joker 2, Jedi, Dinah, Tsunami…). |
| SailRaceHQ | Rolex Fastnet 2023 | HTML | 0.88 | Ready | 17 IRC Overall rows; known top boats (CARO, TEAM JAJO, WARRIOR WON) extracted with correct TCCs. |
| Cowes Week | points2010, class 30 | HTML | 0.88 | Partial | 64 rows extracted but **TCCs all NULL** — points-summary page doesn't carry TCCs; legacy got them from per-race detail pages. |
| Cowes Week | points2025 class 50 (real ingest) | HTML | 0.92 | Partial | 11 rows ingested as `transport='firecrawl'` alongside 11 legacy rows. Place 1 disagrees: legacy=SALVO, firecrawl=SCHERZO OF COWES. event_date off by 1 day. Race-name label differs (legacy "IRC Class 5" vs firecrawl "Overall"). |
| TopYacht | Lincoln Regatta 2026 race 1 | HTML | 0.98 | Ready (but legacy is also fine) | 4 rows. Plan A's plan doc keeps TopYacht on legacy (real URL template, no API ceiling). |
| Sailwave (sailwave.com/...) | EasterChallenge2024/index.html | HTML | 0.0 | Discovery problem | sailwave.com itself doesn't host result pages — they live on individual club domains. Need a seed-crawl loop to find URLs. |

## Aggregate findings

1. **HTML extraction is ready** (0.88–0.98 confidence range). Cowes Week, ISORA, SailRaceHQ produce clean tabular rows that pass the confidence gate.
2. **PDF extraction is not ready** — RHKYC's PDFs flow through Firecrawl as garbled OCR markdown. The confidence gate correctly quarantines them. Firecrawl's experimental PDF mode or a separate PDF-OCR step is needed before RHKYC can switch.
3. **TCC coverage** is the biggest data-quality gap. The per-class points pages on Cowes Week don't carry TCCs; the legacy scraper crawls per-race detail pages to fill them in. Same pattern likely holds for other multi-page sources.
4. **Place-1 disagreement on Cowes Week 2025 class 5** needs human review. Either legacy is stale or Firecrawl mis-ranks rows when the markdown has a non-finisher at the top. One row mismatch out of 11 is on the edge of acceptable; need broader sample.
5. **Discovery is the rate-limiter** for sources like sailwave that have no canonical URL. The `discover-and-ingest` orchestrator + `seed-crawl --aggregators` cron (Plan A) is the right approach but hasn't been exercised yet against real aggregator sites.

## Where we are vs "all the way to Firecrawl"

| Source | Status after this session | Ready to retire legacy? |
|---|---|---|
| sailwave | Net-new coverage (was 0 rows). Needs URL discovery work before scheduled cron. | N/A — no legacy to retire |
| cowesweek | Single-class smoke test green. TCC + place-1 + race-name gaps need investigation before retiring legacy. | No — 1-2 more parallel-run weeks |
| sydneyhobart | Auto-URL disabled (was pointing at the wrong page). Manual ingestion via `--url bwps.cycaracing.com/...` works. | No — needs a stable URL pattern |
| rhkyc | PDF extraction quarantines. Need PDF-OCR pipeline or stick with legacy. | No |
| isora | HTML extraction at 0.95. Ready for parallel-run cron. | Likely yes after 14-day parallel |
| sailracehq | HTML extraction at 0.88. Ready for parallel-run cron. | Likely yes after 14-day parallel |
| topyacht | Legacy URL-template scraper is more reliable than Firecrawl extraction. Keep legacy. | No — keep |
| sailsys | Has a real REST API at api.sailsys.com.au. No Firecrawl advantage. | No — keep |

## Recommended next moves (deferred to operator decision)

1. **Install the merged crontab** so the Firecrawl-parallel entries for ISORA + SailRaceHQ start producing rows alongside legacy. After 14 days, run `irc-data parity-report --source {isora,sailracehq}` and decide on cutover per source.

2. **Investigate Cowes Week per-class TCC fetch**. The legacy scraper must crawl per-race detail pages — find the pattern and either (a) extend the orchestrator to follow detail links automatically, or (b) accept the gap and rely on `boats.cert_number → irc_certificates.tcc` for the TCC fill-in.

3. **Investigate Place-1 disagreement on Cowes Week 2025 class 5**. Either the page shows a DNS/DSQ at top that legacy correctly skipped, or the points table has changed since the legacy scrape.

4. **PDF strategy for RHKYC** — three options:
   - Switch on Firecrawl's PDF mode (`formats=["markdown", "rawHtml"]` with a smarter parser).
   - Keep the legacy PDF parser; only migrate RHKYC's HTML pages.
   - Build a separate PDF-OCR pipeline (pdfplumber + Claude for the extraction step).

5. **Aggregator seed-crawl** — schedule `irc-data seed-crawl --aggregators` nightly to populate `event_discovery` with URLs from RYA fixtures, Australian Sailing, RORC. Then iterate on the discovery → confirm → ingest pipeline.

6. **Decide on sailsys + topyacht** — Plan A's plan doc says keep them. They have real APIs/templates. The user asked for "all the way to Firecrawl"; if that includes these, the migration is multi-month and likely a credit/cost regression. Recommend keeping them on legacy unless there's a specific reliability problem.
