# Reliable, Extensive Boat-Results Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every long-tail race-results scraper onto the Firecrawl + Claude-extractor pipeline, expand coverage to event sites we don't currently touch, add an automated discovery loop for unknown events, and audit which sources need authentication.

**Architecture:** Reuse the in-place Firecrawl pipeline (`discovery.firecrawl_client.scrape_url` → `discovery.extractor.extract_results` → `import_scraper_results`). One CLI verb (`irc-data ingest-event --url URL --source X`) handles every site. Drive scheduled and ad-hoc ingestion from the same path. Add a nightly seed-crawl loop that maps aggregator sites and pushes new event URLs into `event_discovery` for human/AI confirmation.

**Tech stack:** Python 3.11, Firecrawl v2, Claude tool_use (Sonnet 4.6), FastAPI, PostgreSQL 16, pytest. No new dependencies beyond what `discovery/` already pulls in.

**Out of scope.** SailSys + TopYacht scrapers (real APIs, keep them). ORC + IRC certificates (separate plans). RORC scraper (decommissioned).

**Source plan:** `~/.claude/plans/read-this-branch-sharded-nebula.md` (Plan A).
**Related reading:** `~/.claude/plans/2026-05-20-firecrawl-migration.md` — original migration scope.

---

### Task A1: Pre-flight — confirm Firecrawl + extractor are live

**Files:**
- Verify: `~/.env`
- Verify: `api/src/irc_data/discovery/firecrawl_client.py`
- Verify: `api/src/irc_data/discovery/extractor.py:201-305` (`extract_results`)
- Verify: `api/src/irc_data/cli.py:1154-1220` (`ingest-event` CLI)

- [ ] **Step 1: Verify `FIRECRAWL_API_KEY` is in `~/.env` and in 1Password vault.**

```bash
source ~/.env && test -n "$FIRECRAWL_API_KEY" && echo "OK" || echo "MISSING"
```

Expected: `OK`. If MISSING, add via 1Password CLI: `op item get "Firecrawl" --fields api_key` then append to `~/.env`.

- [ ] **Step 2: Smoke-test Firecrawl with a known URL.**

```bash
source ~/.env && curl -s -H "Authorization: Bearer $FIRECRAWL_API_KEY" \
  https://api.firecrawl.dev/v2/scrape \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.sailwave.com/","formats":["markdown"]}' | head -c 300
```

Expected: JSON response with `success: true` and a `markdown` field.

- [ ] **Step 3: Confirm `ingest-event` CLI works end-to-end.**

```bash
cd /home/irc-data/code/sailratings/api && source .venv/bin/activate
irc-data ingest-event --url "https://www.cyca.com.au/results/2024-rolex-sydney-hobart" \
  --source sydneyhobart --dry-run
```

Expected: Dry-run prints extracted `RaceResult[]` JSON without writing to DB.

- [ ] **Step 4: Commit nothing yet; this is a verification step.**

---

### Task A2: Authentication audit of long-tail sources

**Files:**
- Create: `docs/scrapers/auth-audit-2026-05-21.md`

- [ ] **Step 1: For each source, document the auth requirement.**

```bash
for url in \
  "https://www.sailwave.com/results/index.html" \
  "https://www.cyca.com.au/results/2024-rolex-sydney-hobart" \
  "https://www.cowesweek.co.uk/results" \
  "https://www.rhkyc.org.hk/Default.aspx?TabId=358" \
  "https://www.isora.org/index.php/notice-board/results2" \
  "https://www.sailracehq.com/" \
  "https://yachtscoring.com/event_results_archive.cfm" \
  "https://www.rpayc.com.au/sailing/sail-results" ; do
  echo "=== $url ==="
  curl -sI -L -o /dev/null -w "HTTP %{http_code}  size=%{size_download}  redirect=%{url_effective}\n" "$url"
done
```

- [ ] **Step 2: For each redirecting or 401/403 response, manually inspect in a browser.**

Document in `docs/scrapers/auth-audit-2026-05-21.md` with this table:

| Source | URL | Anonymous OK? | Auth method | Notes |
|---|---|---|---|---|
| Sailwave | … | yes/no | none / form / cookie / member | … |
| Cowes Week | … | … | … | … |
| RHKYC | … | … | … | … |
| ISORA | … | … | … | … |
| SailRaceHQ | … | … | … | … |
| YachtScoring | … | … | … | … |
| RPAYC | … | … | … | … |

- [ ] **Step 3: For each "auth required" source, decide:**
  - **Public alternative path:** is there a `/public/` or `/results/` URL with no auth?
  - **Firecrawl session cookie:** Firecrawl supports `headers: {Cookie: "..."}` per request. If a static cookie obtained from one browser login works, store it in 1Password and inject at scrape time.
  - **Skip:** if no public path and no static cookie, mark the source as out-of-scope and note why.

- [ ] **Step 4: Commit the audit doc.**

```bash
git add docs/scrapers/auth-audit-2026-05-21.md
git commit -m "docs(scrapers): authentication audit of long-tail results sources"
```

---

### Task A3: Sailwave migration — proof of pipeline

**Files:**
- Modify: `api/src/irc_data/discovery/extractor.py` (tighten `extract_results` schema if Task A1 surfaced issues)
- Modify: `api/src/irc_data/cli.py` (no changes; `ingest-event` already supports `--source sailwave`)
- Test: `api/tests/test_extractor_sailwave.py` (new)
- Modify: `api/crontab.txt` (no scheduled change; sailwave stays on-demand)

- [ ] **Step 1: Pick three known sailwave events with existing rows in DB.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT source_url, COUNT(*) FROM race_results WHERE source='sailwave' GROUP BY source_url ORDER BY COUNT(*) DESC LIMIT 5;"
```

Pick three URLs. Save them as `URL_A`, `URL_B`, `URL_C` for the next steps.

- [ ] **Step 2: Write the failing test.**

```python
# api/tests/test_extractor_sailwave.py
import pytest
from irc_data.discovery.extractor import extract_results
from irc_data.discovery.firecrawl_client import scrape_url

KNOWN_EVENTS = [
    ("https://example.com/sailwave-A", 25),  # replace with URL_A + expected row count
    ("https://example.com/sailwave-B", 30),
    ("https://example.com/sailwave-C", 18),
]

@pytest.mark.parametrize("url, expected_min_rows", KNOWN_EVENTS)
def test_sailwave_extraction_returns_results(url, expected_min_rows):
    page = scrape_url(url)
    assert page.markdown, "Firecrawl returned empty markdown"
    payload = extract_results(url=url, markdown=page.markdown)
    assert payload.get("_error") is None, f"extractor error: {payload.get('_error')}"
    results = payload["results"]
    assert len(results) >= expected_min_rows, f"got {len(results)}, expected >= {expected_min_rows}"
    for r in results:
        assert r["boat_name"]
        assert r["place"] is not None
        assert r["place"] >= 1
```

- [ ] **Step 3: Run the test — expect it to fail until the URLs and counts are filled in.**

```bash
pytest api/tests/test_extractor_sailwave.py -v
```

- [ ] **Step 4: For each green event, run real ingestion against a fresh DB schema, then diff.**

```bash
irc-data ingest-event --url "$URL_A" --source sailwave
```

Then:

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT boat_name, place, corrected_time FROM race_results WHERE source_url='$URL_A' ORDER BY place;"
```

Eyeball against the page. ≥95% boat-level match, placings off by ≤1 row.

- [ ] **Step 5: Commit.**

```bash
git add api/tests/test_extractor_sailwave.py
git commit -m "test(scrapers): Firecrawl extraction parity for known sailwave events"
```

---

### Task A4: Cowes Week + Sydney-Hobart migration (annual events)

**Files:**
- Modify: `api/src/irc_data/cli.py` (add `--year YYYY` flag to `ingest-event`)
- Test: `api/tests/test_extractor_annual.py` (new)
- Modify: `api/crontab.txt` (add `@yearly` entries)

- [ ] **Step 1: Add `--year YYYY` to `ingest-event` CLI.**

```python
@click.option("--year", type=int, default=None, help="Archive year for annual events (cowesweek, sydneyhobart).")
def ingest_event(url: str, source: str, year: int | None, dry_run: bool, ...):
    if not url and source == "cowesweek" and year:
        url = f"https://www.cowesweek.co.uk/results/{year}"
    if not url and source == "sydneyhobart" and year:
        url = f"https://www.cyca.com.au/results/{year}-rolex-sydney-hobart"
```

- [ ] **Step 2: Write the failing parity test.**

```python
# api/tests/test_extractor_annual.py
import pytest
from irc_data.discovery.extractor import extract_results
from irc_data.discovery.firecrawl_client import scrape_url

@pytest.mark.parametrize("source, year, expected_min", [
    ("cowesweek", 2024, 200),
    ("cowesweek", 2025, 200),
    ("sydneyhobart", 2023, 80),
    ("sydneyhobart", 2024, 80),
])
def test_annual_event_parity(source, year, expected_min):
    url = {"cowesweek": f"https://www.cowesweek.co.uk/results/{year}",
           "sydneyhobart": f"https://www.cyca.com.au/results/{year}-rolex-sydney-hobart"}[source]
    page = scrape_url(url)
    payload = extract_results(url=url, markdown=page.markdown)
    assert payload.get("_error") is None
    assert len(payload["results"]) >= expected_min
```

- [ ] **Step 3: Run, observe Cowes Week sail-number gap.**

```bash
pytest api/tests/test_extractor_annual.py -v
```

If Cowes Week standings lack sail numbers, the extractor will populate `sail_number=null` for most rows. Proceed to Step 4.

- [ ] **Step 4: Add a per-boat detail-page crawl for Cowes Week.**

```python
def enrich_results_with_detail_pages(
    base_url: str,
    results: list[dict],
    detail_url_pattern: str | None,
) -> list[dict]:
    if not detail_url_pattern:
        return results
    for r in results:
        if r.get("detail_url"):
            detail = scrape_url(r["detail_url"])
            extracted = extract_boat_detail(detail.markdown)
            r.update({"sail_number": extracted.get("sail_number")})
    return results
```

- [ ] **Step 5: Add cron entries.**

```text
# api/crontab.txt — annual events
0 6 5 9 * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data ingest-event --source cowesweek --year $(date +\%Y) >> /var/log/irc-data/cowesweek-$(date +\%F).log 2>&1
0 6 30 12 * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data ingest-event --source sydneyhobart --year $(date +\%Y) >> /var/log/irc-data/sydneyhobart-$(date +\%F).log 2>&1
```

- [ ] **Step 6: Install crontab, commit.**

```bash
crontab api/crontab.txt
git add api/src/irc_data/cli.py api/tests/test_extractor_annual.py api/crontab.txt
git commit -m "feat(scrapers): Cowes Week and Sydney-Hobart on Firecrawl with --year flag"
```

---

### Task A5: RHKYC migration (PDF-heavy)

**Files:**
- Modify: `api/src/irc_data/discovery/firecrawl_client.py` (confirm PDF format support)
- Test: `api/tests/test_extractor_rhkyc.py` (new)
- Modify: `api/crontab.txt`

- [ ] **Step 1: Pick 5 recent RHKYC PDFs from existing rows.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT DISTINCT source_url FROM race_results WHERE source='rhkyc' AND source_url ILIKE '%.pdf' ORDER BY source_url DESC LIMIT 5;"
```

- [ ] **Step 2: Write parity test.**

```python
# api/tests/test_extractor_rhkyc.py
PDF_URLS = [...]  # from Step 1

@pytest.mark.parametrize("url", PDF_URLS)
def test_rhkyc_pdf_extraction(url):
    page = scrape_url(url, formats=["markdown"])
    payload = extract_results(url=url, markdown=page.markdown)
    assert payload.get("_error") is None
    assert len(payload["results"]) >= 5
    for r in payload["results"]:
        assert r["boat_name"]
        assert r["place"] is not None
```

- [ ] **Step 3: Run, validate against existing rows, fix prompt if needed.**

```bash
pytest api/tests/test_extractor_rhkyc.py -v
```

- [ ] **Step 4: Update cron — replace bespoke rhkyc scraper with Firecrawl ingestion.**

```text
# api/crontab.txt — REMOVE the line `0 10 * * 3 ... scrape results --source rhkyc`
# REPLACE with weekly discovery+ingestion
0 10 * * 3 cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data discover-and-ingest --seed-url "https://www.rhkyc.org.hk/Default.aspx?TabId=358" --source rhkyc >> /var/log/irc-data/rhkyc-$(date +\%F).log 2>&1
```

Note: `discover-and-ingest` is added in Task A7.

- [ ] **Step 5: Commit.**

```bash
git add api/tests/test_extractor_rhkyc.py
git commit -m "test(scrapers): Firecrawl extraction parity for RHKYC PDF results"
```

---

### Task A6: ISORA + SailRaceHQ migration with 14-day parallel run

**Files:**
- Modify: `api/src/irc_data/scrapers/isora.py` (leave in place)
- Modify: `api/src/irc_data/scrapers/sailracehq.py` (leave in place)
- Modify: `api/crontab.txt`
- Create: `api/alembic/versions/0019_race_results_transport.py`
- Create: `api/src/irc_data/diagnostics/scraper_parity.py`

**Coordination note:** Migration number 0019 assumes Plan C has not yet committed migration 0020. Before writing, check `alembic heads`; if 0019 is taken, use the next free number and update `Revises:`.

- [ ] **Step 1: Add `transport` column to `race_results`.**

```python
# api/alembic/versions/0019_race_results_transport.py
"""add transport column to race_results

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('race_results',
        sa.Column('transport', sa.String(length=32), nullable=True))
    op.create_index('ix_race_results_transport', 'race_results', ['transport'])

def downgrade():
    op.drop_index('ix_race_results_transport', 'race_results')
    op.drop_column('race_results', 'transport')
```

```bash
cd api && source .venv/bin/activate && alembic upgrade head
```

- [ ] **Step 2: Add Firecrawl-side cron entries alongside the existing ones.**

```text
# api/crontab.txt
0 12 * * 2 cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data discover-and-ingest --seed-url "https://www.isora.org/index.php/notice-board/results2" --source isora --tag-as firecrawl >> /var/log/irc-data/isora-firecrawl-$(date +\%F).log 2>&1
0 11 * * 2 cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data discover-and-ingest --seed-url "https://www.sailracehq.com/" --source sailracehq --tag-as firecrawl >> /var/log/irc-data/sailracehq-firecrawl-$(date +\%F).log 2>&1
```

- [ ] **Step 3: Build parity diagnostic CLI.**

```python
# api/src/irc_data/diagnostics/scraper_parity.py
import click
from sqlalchemy import text
from irc_data.db.connection import get_engine

@click.command()
@click.option("--source", required=True)
@click.option("--since", default="14 days")
def parity_report(source, since):
    eng = get_engine()
    sql = text("""
        SELECT
          DATE(scraped_at) AS day,
          COUNT(*) FILTER (WHERE transport='legacy') AS legacy_rows,
          COUNT(*) FILTER (WHERE transport='firecrawl') AS firecrawl_rows,
          COUNT(DISTINCT event_name) AS distinct_events
        FROM race_results
        WHERE source = :source
          AND scraped_at >= NOW() - INTERVAL :since
        GROUP BY DATE(scraped_at)
        ORDER BY day DESC;
    """)
    with eng.connect() as conn:
        for row in conn.execute(sql, {"source": source, "since": since}):
            click.echo(f"{row.day}  legacy={row.legacy_rows:5d}  firecrawl={row.firecrawl_rows:5d}  events={row.distinct_events}")
```

- [ ] **Step 4: Halt-and-checkpoint. Parallel-run gate.**

After 14 days of green parity, retire legacy entries from cron and delete the old scraper files:

```bash
git rm api/src/irc_data/scrapers/isora.py api/src/irc_data/scrapers/sailracehq.py
git add api/crontab.txt
git commit -m "feat(scrapers): retire bespoke ISORA + SailRaceHQ; Firecrawl is source of truth"
```

**If executing this plan in a single session, STOP HERE after Step 3 and surface the 14-day gate to the user.**

---

### Task A7: Discovery seed-crawl orchestrator

**Files:**
- Create: `api/src/irc_data/discovery/orchestrator.py`
- Modify: `api/src/irc_data/cli.py`
- Modify: `api/crontab.txt`

- [ ] **Step 1: Build `discover-and-ingest` CLI.**

```python
# api/src/irc_data/cli.py
@cli.command("discover-and-ingest")
@click.option("--seed-url", required=True)
@click.option("--source", required=True)
@click.option("--max-pages", default=20)
@click.option("--tag-as", default="firecrawl")
def discover_and_ingest(seed_url, source, max_pages, tag_as):
    """Crawl a seed URL, extract result URLs, ingest each one."""
    from irc_data.discovery.orchestrator import seed_crawl_and_ingest
    seed_crawl_and_ingest(seed_url, source, max_pages, transport_tag=tag_as)
```

- [ ] **Step 2: Build the orchestrator.**

```python
# api/src/irc_data/discovery/orchestrator.py
from irc_data.discovery.firecrawl_client import map_site, scrape_url
from irc_data.discovery.extractor import extract_results
from irc_data.scrapers.result_import import import_scraper_results
from irc_data.db.connection import get_engine

def seed_crawl_and_ingest(seed_url: str, source: str, max_pages: int, transport_tag: str):
    eng = get_engine()
    urls = map_site(seed_url, limit=max_pages)
    for url in urls:
        page = scrape_url(url)
        if not page or not page.markdown:
            continue
        payload = extract_results(url=url, markdown=page.markdown)
        if payload.get("_error"):
            continue
        results = payload["results"]
        import_scraper_results(eng, results, source=source, transport=transport_tag, source_url=url)
```

- [ ] **Step 3: Add nightly seed-crawl entry.**

```text
30 22 * * * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data seed-crawl --aggregators >> /var/log/irc-data/seedcrawl-$(date +\%F).log 2>&1
```

```python
@cli.command("seed-crawl")
@click.option("--aggregators", is_flag=True)
def seed_crawl(aggregators):
    AGGREGATORS = [
        "https://www.rya.org.uk/racing/fixtures",
        "https://www.australiansailing.org/events",
        "https://www.rorc.org/events",
    ]
    if aggregators:
        for seed in AGGREGATORS:
            urls = map_site(seed, limit=50)
            for url in urls:
                upsert_event_discovery(url, source_hint=None, status="pending")
```

- [ ] **Step 4: Commit.**

```bash
git add api/src/irc_data/discovery/orchestrator.py api/src/irc_data/cli.py api/crontab.txt
git commit -m "feat(discovery): orchestrator + seed-crawl loop against aggregator sites"
```

---

### Task A8: New sources we don't cover — first wave

**Files:**
- Modify: `api/src/irc_data/cli.py` (no change; rely on `ingest-event` and `discover-and-ingest`)
- Create: `docs/scrapers/new-sources-2026-05-21.md`

- [ ] **Step 1: Build a target list of new sources.**

| Region | Source | URL pattern | Auth |
|---|---|---|---|
| US | YachtScoring | https://yachtscoring.com/event_results.cfm | no |
| AU | RPAYC | https://www.rpayc.com.au/sailing/sail-results | likely no |
| AU | CYCA series | https://www.cyca.com.au/results | no |
| UK | RORC fixture-listed events | https://www.rorc.org/events | no |
| Med | YCM | https://www.ycm.org/results | unknown |

- [ ] **Step 2: For each "auth=no" source, run discover-and-ingest as a one-shot.**

```bash
irc-data discover-and-ingest --seed-url "https://yachtscoring.com/event_results_archive.cfm" --source yachtscoring --max-pages 50
```

- [ ] **Step 3: Add the source to `crontab.txt` if it should run regularly.**

- [ ] **Step 4: Commit per-source.**

---

### Task A9: Verification — end-to-end smoke test

- [ ] **Step 1: Run all Firecrawl-based scrapers manually and observe row counts.**

```bash
for src in sailwave cowesweek sydneyhobart rhkyc isora sailracehq yachtscoring ; do
  irc-data discover-and-ingest --seed-url "<seed>" --source "$src" --max-pages 10
done
```

- [ ] **Step 2: Run parity diagnostic for each source.**

```bash
for src in isora sailracehq rhkyc ; do
  irc-data parity-report --source "$src"
done
```

- [ ] **Step 3: Confirm `/justin/firecrawl` dashboard shows expected credit burn rate.**

- [ ] **Step 4: Tag and push.**

```bash
git tag scrapers-firecrawl-cutover-v1
git push --tags
```
