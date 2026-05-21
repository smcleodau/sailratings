# New Results Sources — First Wave (2026-05-21)

**Context:** Plan A (`docs/superpowers/plans/2026-05-21-results-extractor-extensive.md`),
Task A8. Now that `discover-and-ingest` and `ingest-event` cover every existing
long-tail source, expand coverage to sources we don't currently touch.

## Target list

| Region | Source        | URL pattern                                                         | Auth | Pipeline command |
|--------|---------------|---------------------------------------------------------------------|------|-------------------|
| US     | YachtScoring  | https://yachtscoring.com/event_results.cfm?eid={n}                  | no   | `ingest-event --source yachtscoring --url …` (per-event), or `discover-and-ingest --seed-url https://yachtscoring.com/event_results_archive.cfm --source yachtscoring` |
| AU     | RPAYC         | per-event WordPress posts on rpayc.com.au; no canonical index        | no   | `ingest-event --source rpayc --url …` (per-event) |
| AU     | CYCA series   | https://cyca.com.au/results (and bwps.cycaracing.com sub-domain)     | no   | `discover-and-ingest --seed-url https://cyca.com.au/results --source sydneyhobart` for the annual ocean race; series races require a separate seed URL once published |
| UK     | RORC fixtures | https://www.rorc.org/events                                          | no   | Aggregator only — seed-crawl picks it up nightly; per-event results land on sailracehq.com which is already covered |
| Med    | YCM           | https://www.ycm.org/results                                          | unknown — see audit | hold; needs probe |

## Per-source plan

### YachtScoring (US — large coverage, many events)
- **Status:** Auth-free; archive page at `event_results_archive.cfm` enumerates ~hundreds of events going back several years.
- **Initial pull:** `irc-data discover-and-ingest --seed-url "https://yachtscoring.com/event_results_archive.cfm" --source yachtscoring --max-pages 50`
  - Run once, then add a weekly cron at low priority (Sunday 13:00 UTC).
- **Risk:** YachtScoring sometimes renders results in iframes / ASPX views — Firecrawl's rendered scrape should still produce useful markdown but we should sample-check 5 events before trusting.

### RPAYC (Royal Prince Alfred Yacht Club, AU)
- **Status:** The plan's URL (`/sailing/sail-results`) 404s; results are now blog posts on the main rpayc.com.au site.
- **Initial pull:** event-by-event via `ingest-event --source rpayc --url …` as URLs surface from the nightly aggregator seed-crawl (Australian Sailing fixtures).
- **No cron** — too low-volume to justify a dedicated schedule; rely on aggregator discovery.

### CYCA series (Sydney non-Hobart races)
- **Status:** The Sydney Hobart annual is already covered (Task A4). Series racing (Blue Water Pointscore etc.) lives on the bwps.cycaracing.com sub-domain.
- **Initial pull:** `irc-data ingest-event --source sydneyhobart --url "https://bwps.cycaracing.com/standings?series=YYYY"` for each finished BWPS series.
- **Cron:** monthly during the racing season (October–April). To be added when the first season is validated.

### RORC fixtures (UK aggregator)
- **Status:** Already in `DEFAULT_AGGREGATORS` for the nightly `seed-crawl`. Per-event RORC results have moved to sailracehq.com (already covered in Task A6).
- **Action:** nothing new beyond Task A7's wiring.

### YCM (Yacht Club de Monaco — Mediterranean)
- **Status:** Auth status unknown — needs a manual probe in a browser and a 1Password cookie if a member portal is required. Out of scope for the first wave.

## Recommendation

1. Run the YachtScoring `discover-and-ingest` against the archive page first; sample-check 5 events; if green, add cron.
2. Let the nightly seed-crawl surface RPAYC + small AU clubs naturally — confirm each in `/justin/discovery` and ingest with `discover-events --auto-ingest` once the platform is recognised.
3. Defer YCM until someone has a logged-in browser session to probe.

No code change in this task — the pipeline (Firecrawl + `extract_results` + `import_scraper_results`) is identical for every source; the only per-source decision is which seed URL to point at and whether the cron cadence is weekly / monthly / on-demand.
