# Firecrawl Cutover Runbook

When a long-tail source meets the quantitative quality gate, this runbook
documents how to retire the bespoke legacy scraper and run Firecrawl-only.

> **OPS-02-06 update (2026-09-03):** the parity gate and cutover are now
> automated. Prefer the CLIs over the manual SQL below:
>
> - `irc-data parity-gate --source X` — evaluates the OPS-02-06 gate
>   (14-day window, row capture ≥ 95%, place-1 agreement ≥ 98%, min 5
>   comparable observations). Exits non-zero on FAIL so it can gate a cron.
>   Add `--save` to persist a snapshot into `firecrawl_diffs` for the
>   14-day evidence trail, and `--json` for machine-readable output.
> - `irc-data cutover-status` — shows, per source, whether the adapter is
>   the Firecrawl pipeline, whether legacy is paused, and the 14-day
>   `transport` split (the "rows arrive with transport='firecrawl'" proof).
> - `irc-data cutover-source X` — performs the cutover **only if the gate
>   passes**: pauses the legacy adapter, repoints
>   `data_sources.adapter_class` at the Firecrawl discovery pipeline
>   (`irc_data.discovery.orchestrator.seed_crawl_and_ingest`), and writes an
>   `ingest_events` audit row. `--dry-run` previews; `--force` overrides a
>   failing gate (audited).
>
> The manual procedure below remains as the underlying reference.

---

## Cutover Quality Gate

A source is ready to cutover when, across the most recent 14-day window:

| Metric | Threshold |
|--------|-----------|
| Distinct URLs sampled | ≥ 20 |
| Mean recall | ≥ 0.85 |
| P10 recall (10th-percentile) | ≥ 0.75 |

Pages where the extractor returns `_error` count as recall = 0.

The OPS-02-06 automated gate (`parity-gate`) adds the parallel-run
comparison: over the same 14-day window, Firecrawl row capture
(`transport='firecrawl'` / `transport='legacy'` rows) must be ≥ 0.95 and
place-1 (winner) agreement must be ≥ 0.98.

**Gate query:**

```sql
SELECT
    source,
    COUNT(*)                                                          AS urls,
    ROUND(AVG(match_rate)::numeric, 3)                               AS mean_recall,
    ROUND(
        percentile_cont(0.10) WITHIN GROUP (ORDER BY match_rate)::numeric,
        3
    )                                                                AS p10_recall
FROM firecrawl_diffs
WHERE ran_at >= NOW() - INTERVAL '14 days'
  AND legacy_rows IS NOT NULL AND legacy_rows > 0
GROUP BY source
ORDER BY mean_recall DESC;
```

Run this after T6 re-tests each source and again after any further prompt or
expander tuning.

---

## Per-Source Retire-Legacy Procedure

Once a source passes the gate, follow these steps in order:

1. **Remove the legacy cron entry** from `api/crontab.txt`. Comment it out
   with a `# RETIRED YYYY-MM-DD` note rather than deleting — keeps the audit
   trail.

2. **Ensure the Firecrawl cron entry is present** in `api/crontab.txt`.
   Example for ISORA (daily 05:30 UTC):
   ```
   30 5 * * * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data discover-and-ingest --source isora --seed-url https://www.isora.org/notice-board/results2 --max-pages 30 >> /home/irc-data/logs/isora-fc.log 2>&1
   ```

3. **Archive the bespoke scraper** — move to `src/irc_data/scrapers/legacy/`
   rather than deleting. Keep for 30 days in case a rollback is needed.
   ```bash
   mkdir -p src/irc_data/scrapers/legacy
   git mv src/irc_data/scrapers/<source>.py src/irc_data/scrapers/legacy/
   ```

4. **Install the updated crontab:**
   ```bash
   crontab api/crontab.txt
   ```

5. **Watch parity for 7 days** — run `firecrawl-diff --source <source>`
   daily and confirm row counts are stable or growing. If mean recall drops
   below 0.80 on any day, pause and investigate before the legacy scraper
   retention window closes.

---

## Decision Matrix for Non-Passing Sources

| Condition | Interpretation | Action |
|-----------|---------------|--------|
| Mean ≥ 0.85, P10 < 0.75 | Tail events under-extract; bulk is fine | Fix URL expander or prompt for the tail URLs; do not cutover yet |
| Mean 0.75–0.85 | Systemic partial extraction | Investigate page structure; check for JS-rendered content or PDF pages; do not cutover |
| Mean < 0.75 | Source not suitable for Firecrawl as-is | Consider PDF pipeline (pdfplumber → Claude), or keep legacy scraper indefinitely |
| URLs sampled < 20 | Not enough data | Run `firecrawl-diff --source <source> --limit 20 --days 9999` to build up the sample |

---

## Quarantine Review

Quarantined extractions (confidence gate or recall gate failures) land in
`ingest_events` with `status='quarantined'`. Review periodically to spot
persistent patterns:

```sql
SELECT
    source,
    reference AS url,
    reason,
    created_at
FROM ingest_events
WHERE status = 'quarantined'
  AND created_at >= NOW() - INTERVAL '14 days'
ORDER BY created_at DESC;
```

Common quarantine causes:
- **confidence < 0.70** — page structure too ambiguous; extractor uncertain.
  Check if Firecrawl is returning garbled markdown (JS-heavy site, PDF with
  bad OCR).
- **recall_est < 0.75** — extractor returned far fewer boats than the legacy
  baseline. Usually means a multi-class page that needs a URL expander or a
  chunker regex update.

---

## Sources and Status (as of 2026-05-22)

| Source | Legacy scraper | Firecrawl mode | Gate status |
|--------|---------------|----------------|-------------|
| ISORA | `legacy/isora.py` | `discover-and-ingest --source isora` | Chunker regex updated to support "Class 0/1/2" formats |
| SailRaceHQ | `legacy/sailracehq.py` | `discover-and-ingest --source sailracehq` | Pending JS-rendering or API endpoint integration |
| Cowes Week | `legacy/cowesweek.py` | `discover-and-ingest --source cowesweek --mode per-source-expand --year YYYY` | Chunker & name-matching parity pending review |
| RHKYC | `legacy/rhkyc.py` (retired) | `discover-and-ingest --source rhkyc` | **Completed (2026-05-22)** - Legacy scraper archived |
| SailSys | `sailsys.py` | **Not migrating** — structured API, no Firecrawl ROI | N/A |
| TopYacht | `topyacht.py` | **Not migrating** — URL templates, no Firecrawl ROI | N/A |

Update the Gate status column as more sources are successfully transitioned.
