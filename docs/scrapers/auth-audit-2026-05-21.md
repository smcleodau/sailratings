# Long-tail Scraper Authentication Audit — 2026-05-21

**Context:** Plan A (`docs/superpowers/plans/2026-05-21-results-extractor-extensive.md`).
Inventory of every results source we want to put behind the Firecrawl + Claude extractor pipeline, with a record of whether the public URL needs login/cookie/member auth.

## Probe results

`curl -sI -L` against each candidate URL (2026-05-21):

| Source       | Probed URL                                                         | HTTP | Redirected to                                                                 |
|--------------|--------------------------------------------------------------------|------|--------------------------------------------------------------------------------|
| Sailwave     | https://www.sailwave.com/results/index.html                        | 404  | unchanged — the index path was retired                                         |
| CYCA         | https://www.cyca.com.au/results/2024-rolex-sydney-hobart            | 200  | https://cyca.com.au/event/2024-rolex-sydney-hobart-yacht-race-entries-close/   |
| Cowes Week   | https://www.cowesweek.co.uk/results                                | 200  | https://www.cowesweek.co.uk/web/code/php/main_c.php?section=results            |
| RHKYC        | https://www.rhkyc.org.hk/Default.aspx?TabId=358                    | 200  | https://www.rhkyc.org.hk/ (legacy URL — now a home-page redirect)              |
| ISORA        | https://www.isora.org/index.php/notice-board/results2              | 200  | unchanged                                                                      |
| SailRaceHQ   | https://www.sailracehq.com/                                        | 403  | https://sailracehq.com/ (Cloudflare bot check returns 403 to bare curl)        |
| YachtScoring | https://yachtscoring.com/event_results_archive.cfm                 | 200  | unchanged                                                                      |
| RPAYC        | https://www.rpayc.com.au/sailing/sail-results                      | 404  | https://rpayc.com.au/sailing/sail-results (path no longer exists)              |

## Auth-status table

| Source        | Canonical URL                                                                                                       | Anonymous OK? | Auth method                                | Notes |
|---------------|---------------------------------------------------------------------------------------------------------------------|---------------|--------------------------------------------|-------|
| Sailwave      | published per-event Sailwave HTML pages (no canonical index; URLs live on club sites)                                | yes           | none                                       | The `sailwave.com/results/index.html` directory is retired. Treat Sailwave as an *event-shape* whose URL is given to us per-event. We already store `source_url` per row, so the long tail is fed by hand or via aggregator crawl. |
| Cowes Week    | https://www.cowesweek.co.uk/results (annual)                                                                          | yes           | none                                       | Public; Firecrawl scrapes cleanly. Annual event, scrape once per September. |
| Sydney–Hobart | https://cyca.com.au/event/{year}-rolex-sydney-hobart-yacht-race / standings page on bwps.cycaracing.com                | yes           | none                                       | Public. CYCA standings live on a sub-domain — Firecrawl needs the standings URL, not the marketing page. |
| RHKYC         | https://www.rhkyc.org.hk/sailing-results                                                                              | yes           | none                                       | The plan's `Default.aspx?TabId=358` URL is legacy and now redirects home. Use `/sailing-results` and the underlying `/storage/app/media/Sailing/result/{EVENT}/{YEAR}/{FILE}.pdf` PDFs. |
| ISORA         | https://www.isora.org/index.php/notice-board/results2                                                                  | yes           | none                                       | Public Joomla CMS page; Firecrawl handles. |
| SailRaceHQ    | https://sailracehq.com/                                                                                               | yes (via Firecrawl) | Cloudflare bot challenge for naive HTTP | Bare `curl` is 403 — Cloudflare. Firecrawl bypasses by default (rendered browser). No login required. |
| YachtScoring  | https://yachtscoring.com/event_results_archive.cfm                                                                    | yes           | none                                       | Public; per-event pages use `event_results.cfm?eid={n}`. |
| RPAYC         | (deprecated path; replaced by event-specific WordPress posts on rpayc.com.au)                                          | yes           | none                                       | The `/sailing/sail-results` URL 404s. RPAYC publishes per-event results as blog posts. Treat as Sailwave-shape: feed URLs in per event from aggregator discovery. |

## Per-source decision

- **Sailwave** — no canonical index; ingest per-event URLs via `ingest-event`. No auth needed.
- **Cowes Week** — annual; cron at end of August with `ingest-event --source cowesweek --year YYYY`. No auth needed.
- **Sydney–Hobart** — annual; cron 30 December with `ingest-event --source sydneyhobart --year YYYY`. No auth needed.
- **RHKYC** — weekly cron on `/sailing-results` index + walk PDF links. No auth needed.
- **ISORA** — weekly cron via `discover-and-ingest` against `/index.php/notice-board/results2`. No auth needed.
- **SailRaceHQ** — weekly cron via `discover-and-ingest` against the home page. Cloudflare-fronted but Firecrawl's rendered scrape bypasses the bot check.
- **YachtScoring** — on-demand `ingest-event --source yachtscoring --url ...`; later, `discover-and-ingest` against the archive. No auth needed.
- **RPAYC** — per-event ingestion (Sailwave-shape). No auth needed.

## Skipped sources

None at this point — every source above is reachable anonymously, so no static-cookie injection is required. Should that change (e.g. a club moves a results page behind a member portal), the strategy will be:

1. Capture a logged-in session cookie from a single browser session.
2. Store the cookie in 1Password under `scrapers/<source>-cookie`.
3. Inject via Firecrawl's `headers: {Cookie: "..."}` per-request kwarg at scrape time.

## Recommendation for plan execution

Plan A's URL list referenced a few legacy paths (`sailwave.com/results/index.html`, `rhkyc.org.hk/Default.aspx?TabId=358`, `rpayc.com.au/sailing/sail-results`). Use the **canonical URLs** in the table above when wiring cron jobs and tests rather than the URLs the plan template hard-coded.
