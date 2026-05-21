# Historical IRC Certificate Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover IRC certificates from before 2024 (we currently hold only 3,809 certs all dated 2024-07-11 to 2025-12-31). Combine three existing-but-disconnected strategies into one orchestrator: TCC CSV-derived URL probing (`historical_certs.py`), Wayback Machine bulk discovery (`wayback.py`), and backward cert-number scanning (`cert_probe.py`). Step-zero is pulling historical TCC listings from Wayback so we know which cert numbers ever existed.

**Architecture:** Three strategies, one CLI verb (`irc-data backfill-irc-certs`). Strategies run in sequence: TCC archive harvest → cert-number enumeration → live + Wayback PDF probe → parse + attach. Resumable state in `.backfill_state.json`.

**Tech stack:** Python 3.11, httpx (async), pdfplumber, BeautifulSoup, Click, PostgreSQL. No new deps.

**Out of scope.** ORC certificates. Race results. Active 2024+ certs (already healthy via `irc-data scrape certs`).

**Source plan:** `~/.claude/plans/read-this-branch-sharded-nebula.md` (Plan B).

---

### Task B1: Audit current state and define success metric

**Files:**
- Read-only: `api/src/irc_data/scrapers/historical_certs.py`, `wayback.py`, `cert_probe.py`
- Create: `docs/superpowers/working-notes/irc-historical-baseline.md`

- [ ] **Step 1: Baseline metrics.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT EXTRACT(YEAR FROM issue_date) AS yr, COUNT(*) FROM irc_certificates GROUP BY yr ORDER BY yr;"
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FROM irc_certificates WHERE boat_id IS NULL;"
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(DISTINCT cert_number) FROM irc_certificates;"
```

Record results in `docs/superpowers/working-notes/irc-historical-baseline.md`.

- [ ] **Step 2: Set targets.**

Target end-state (12 months of backfill effort):
- At least 8,000 historical certs (issue_date 2010–2023) parsed into `irc_certificates`.
- ≥80% of those linked to a `boats` row.
- Resume state survives session restarts.

- [ ] **Step 3: Commit baseline.**

```bash
git add docs/superpowers/working-notes/irc-historical-baseline.md
git commit -m "docs(certs): IRC historical backfill baseline"
```

---

### Task B2: Harvest historical TCC listings from Wayback

**Files:**
- Modify: `api/src/irc_data/scrapers/wayback.py` (add `harvest_tcc_archives()` function)
- Modify: `api/src/irc_data/cli.py` (new `wayback-tcc` subcommand)
- Test: `api/tests/test_wayback_tcc.py` (new)

- [ ] **Step 1: Write the failing test.**

```python
# api/tests/test_wayback_tcc.py
from irc_data.scrapers.wayback import harvest_tcc_archives

def test_harvest_tcc_finds_multiple_years(tmp_path):
    """Wayback should yield TCC CSV/HTML for years 2010-2025."""
    archives = harvest_tcc_archives(start_year=2010, end_year=2025, out_dir=tmp_path)
    years_found = {a["year"] for a in archives}
    assert len(years_found) >= 8, f"only found {years_found}"
    for a in archives:
        assert a["path"].exists(), f"file missing: {a['path']}"
```

- [ ] **Step 2: Run — expect import error.**

```bash
pytest api/tests/test_wayback_tcc.py -v
```

- [ ] **Step 3: Implement.**

```python
# api/src/irc_data/scrapers/wayback.py — add function

WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
IRC_TCC_PATTERNS = [
    "https://ircrating.org/wp-content/uploads/*/ClubListing*.csv",
    "https://ircrating.org/wp-content/uploads/*/tcc-listing*.csv",
    "https://ircrating.org/irc-racing/online-tcc-listings/",
]

async def harvest_tcc_archives(start_year: int, end_year: int, out_dir: Path) -> list[dict]:
    """Query Wayback CDX for TCC listings between start_year and end_year.
       Download each unique snapshot, store as out_dir/tcc_{year}_{timestamp}.csv.
       Return list of {year, timestamp, original_url, path}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    async with get_http_client() as client:
        for pattern in IRC_TCC_PATTERNS:
            params = {
                "url": pattern,
                "from": f"{start_year}0101",
                "to": f"{end_year}1231",
                "output": "json",
                "fl": "timestamp,original",
                "collapse": "timestamp:6",
            }
            r = await client.get(WAYBACK_CDX_URL, params=params)
            rows = r.json()[1:]
            for ts, original in rows:
                year = int(ts[:4])
                snap_url = f"https://web.archive.org/web/{ts}id_/{original}"
                target = out_dir / f"tcc_{year}_{ts}.csv"
                if target.exists():
                    continue
                content = (await client.get(snap_url)).content
                target.write_bytes(content)
                results.append({"year": year, "timestamp": ts, "original_url": original, "path": target})
    return results
```

- [ ] **Step 4: Run test — expect PASS.**

- [ ] **Step 5: Run for real and inspect.**

```bash
irc-data wayback-tcc --start-year 2010 --end-year 2025
ls api/data/raw/tcc_listings/historical/ | wc -l
```

Expected: 50+ CSV files across 16 years.

- [ ] **Step 6: Commit.**

```bash
git add api/src/irc_data/scrapers/wayback.py api/src/irc_data/cli.py api/tests/test_wayback_tcc.py
git commit -m "feat(scrapers): Wayback harvest of historical IRC TCC listings"
```

---

### Task B3: Build a master cert-number index from harvested TCCs

**Files:**
- Modify: `api/src/irc_data/parsers/csv_tcc.py` (or extend `historical_certs.py` line 114-153)
- Create: `api/src/irc_data/scrapers/cert_index.py`
- Test: `api/tests/test_cert_index.py` (new)

- [ ] **Step 1: Failing test.**

```python
# api/tests/test_cert_index.py
from irc_data.scrapers.cert_index import build_index_from_tcc_dir
from pathlib import Path

def test_index_contains_pre_2024_certs():
    idx = build_index_from_tcc_dir(Path("api/data/raw/tcc_listings/historical"))
    assert len(idx) >= 5000, f"only {len(idx)} certs found"
    by_year = {}
    for entry in idx:
        by_year.setdefault(entry["year"], 0)
        by_year[entry["year"]] += 1
    assert any(y < 2024 for y in by_year)
```

- [ ] **Step 2: Implement.**

```python
# api/src/irc_data/scrapers/cert_index.py
import csv
from pathlib import Path
from typing import Iterator

def build_index_from_tcc_dir(dir_path: Path) -> list[dict]:
    """Parse all CSVs in dir_path. Return deduped list of:
       {cert_number, boat_name, sail_number, year}.
    """
    seen: dict[str, dict] = {}
    for csv_path in dir_path.glob("tcc_*.csv"):
        year = int(csv_path.stem.split("_")[1])
        for row in _read_tcc_csv(csv_path):
            key = row["cert_number"]
            if key not in seen or year > seen[key]["year"]:
                seen[key] = {**row, "year": year}
    return list(seen.values())

def _read_tcc_csv(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cert = (row.get("CertNo") or row.get("Cert No") or row.get("cert_number") or "").strip()
            if not cert:
                continue
            yield {
                "cert_number": cert,
                "boat_name": (row.get("BoatName") or row.get("Boat Name") or "").strip(),
                "sail_number": (row.get("SailNo") or row.get("Sail No") or "").strip(),
            }
```

- [ ] **Step 3: Run test.**

- [ ] **Step 4: Commit.**

```bash
git add api/src/irc_data/scrapers/cert_index.py api/tests/test_cert_index.py
git commit -m "feat(certs): build master IRC cert-number index from historical TCC listings"
```

---

### Task B4: Multi-strategy PDF probe orchestrator

**Files:**
- Create: `api/src/irc_data/scrapers/irc_backfill.py`
- Modify: `api/src/irc_data/cli.py` (new `backfill-irc-certs` command)
- Test: `api/tests/test_irc_backfill.py` (new)

- [ ] **Step 1: Failing test.**

```python
# api/tests/test_irc_backfill.py
from irc_data.scrapers.irc_backfill import probe_cert
import pytest, re

@pytest.mark.asyncio
async def test_probe_cert_finds_via_wayback_when_live_missing(httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*ircrating.org/pdfdirectory/.*"), status_code=404)
    httpx_mock.add_response(
        url=re.compile(r".*web.archive.org/web/.*"),
        content=b"%PDF-1.4 fake pdf content",
    )
    result = await probe_cert(cert_number="GBR1234R", boat_name="Test", sail_number="GBR1234", year=2018)
    assert result["source"] == "wayback"
    assert result["pdf_path"].exists()
```

- [ ] **Step 2: Implement the orchestrator.**

```python
# api/src/irc_data/scrapers/irc_backfill.py
from pathlib import Path
from irc_data.scrapers.historical_certs import (
    build_pdf_url_candidates,
)
from irc_data.scrapers.wayback import lookup_pdf_in_wayback
from irc_data.scrapers.base import get_http_client, fetch_with_retry

PROBE_STATE_FILE = Path("api/data/raw/.irc_backfill_state.json")

async def probe_cert(cert_number: str, boat_name: str, sail_number: str, year: int) -> dict:
    """Try in order: (1) live IRC PDF directory, (2) Wayback snapshot.
       Return {source, pdf_path, status} or {source: None, status: 'not_found'}.
    """
    candidates = build_pdf_url_candidates(cert_number, boat_name, sail_number)
    async with get_http_client() as client:
        for url in candidates:
            r = await client.head(url)
            if r.status_code == 200:
                content = (await client.get(url)).content
                path = _save_pdf(content, cert_number)
                return {"source": "live", "pdf_path": path, "status": "found"}
        for url in candidates:
            way = await lookup_pdf_in_wayback(url)
            if way:
                path = _save_pdf(way["content"], cert_number, prefix="wayback")
                return {"source": "wayback", "pdf_path": path, "status": "found"}
    return {"source": None, "status": "not_found"}

async def backfill_from_index(index: list[dict], resume: bool = True) -> dict:
    state = _load_state() if resume else {"done": []}
    stats = {"found_live": 0, "found_wayback": 0, "not_found": 0}
    for entry in index:
        if entry["cert_number"] in state["done"]:
            continue
        result = await probe_cert(**entry)
        if result["status"] == "found":
            stats[f"found_{result['source']}"] += 1
        else:
            stats["not_found"] += 1
        state["done"].append(entry["cert_number"])
        _save_state(state)
    return stats
```

- [ ] **Step 3: Wire CLI.**

```python
# api/src/irc_data/cli.py
@cli.command("backfill-irc-certs")
@click.option("--strategy", type=click.Choice(["all", "live", "wayback", "csv"]), default="all")
@click.option("--no-resume", is_flag=True)
@click.option("--limit", type=int, default=None, help="Cap number of certs probed (for testing)")
def backfill_irc_certs(strategy, no_resume, limit):
    import asyncio
    from irc_data.scrapers.cert_index import build_index_from_tcc_dir
    from irc_data.scrapers.irc_backfill import backfill_from_index

    idx = build_index_from_tcc_dir(Path("api/data/raw/tcc_listings/historical"))
    if limit:
        idx = idx[:limit]
    stats = asyncio.run(backfill_from_index(idx, resume=not no_resume))
    click.echo(f"Found live: {stats['found_live']}, wayback: {stats['found_wayback']}, missing: {stats['not_found']}")
```

- [ ] **Step 4: Run on a 50-cert sample.**

```bash
irc-data backfill-irc-certs --limit 50
```

- [ ] **Step 5: Commit.**

```bash
git add api/src/irc_data/scrapers/irc_backfill.py api/src/irc_data/cli.py api/tests/test_irc_backfill.py
git commit -m "feat(certs): multi-strategy IRC historical backfill orchestrator"
```

---

### Task B5: Parse + attach harvested PDFs to boats

**Files:**
- Modify: `api/src/irc_data/cli.py:parse_certs` (sweep the historical PDF dir)
- Modify: `api/src/irc_data/parsers/certificate_pdf.py` (handle older-format PDFs if needed)
- Test: `api/tests/test_certificate_pdf_historical.py` (new)

- [ ] **Step 1: Run existing parse-certs on harvested PDFs.**

```bash
irc-data parse-certs --include-historical
```

If the flag doesn't exist, add it:

```python
@cli.command("parse-certs")
@click.option("--include-historical", is_flag=True)
def parse_certs(include_historical):
    dirs = [Path("api/data/raw/certificates")]
    if include_historical:
        dirs.append(Path("api/data/raw/certificates/historical"))
    for d in dirs:
        for pdf in d.rglob("*.pdf"):
            cert = parse_pdf(pdf)
            upsert_irc_certificate(eng, cert)
```

- [ ] **Step 2: Measure parse failure rate.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FROM irc_certificates WHERE issue_date IS NULL;"
```

If > 5%, extend the parser to handle older layouts.

- [ ] **Step 3: Re-run match-boats to attach newly-parsed certs.**

```bash
irc-data match-boats
```

- [ ] **Step 4: Validate counts.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT EXTRACT(YEAR FROM issue_date) AS yr, COUNT(*) FROM irc_certificates GROUP BY yr ORDER BY yr;"
```

Expect counts > 0 for years 2010–2023.

- [ ] **Step 5: Commit.**

```bash
git add api/src/irc_data/cli.py api/src/irc_data/parsers/certificate_pdf.py api/tests/test_certificate_pdf_historical.py
git commit -m "feat(certs): parse harvested historical IRC PDFs and attach to boats"
```

---

### Task B6: Verification

- [ ] **Step 1: Confirm targets met.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data <<SQL
SELECT
  (SELECT COUNT(*) FROM irc_certificates WHERE issue_date < '2024-01-01') AS historical_certs,
  (SELECT COUNT(*) FROM irc_certificates WHERE boat_id IS NOT NULL) * 100.0
    / NULLIF((SELECT COUNT(*) FROM irc_certificates), 0) AS pct_linked;
SQL
```

Target: historical_certs ≥ 5,000, pct_linked ≥ 70%.

- [ ] **Step 2: Tag.**

```bash
git tag irc-historical-backfill-v1
git push --tags
```
