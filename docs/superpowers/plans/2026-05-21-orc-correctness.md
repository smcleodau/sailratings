# ORC Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 7-day boat-link orphan gap, schedule the ORC detail backfill so the 73% missing GPH/polars get filled in within 14 days, harden ORC sail-number normalisation to match the IRC-side prefix handling, and add per-cert error logging + an orphan diagnostic CLI so we can see what isn't linking and why.

**Architecture:** Four atomic changes:
1. **Cron**: add daily `match-boats --orc-only` after the 03:00 scrape; add daily `scrape orc-detail --backlog` until caught up.
2. **Matching**: lift `normalize_sail_tokens` (currently in race-results matchers) into a shared module and call it from `match_orc_to_irc`.
3. **Logging**: capture per-cert ingest failures + per-cert match failures into the existing `ingest_events` table (or create one if absent).
4. **Diagnostic CLI**: `irc-data report orc-orphans` + `irc-data report orc-detail-coverage`.

**Tech stack:** Python 3.11, SQLAlchemy, Click, PostgreSQL. No new deps.

**Out of scope.** ORC scraper rewrite (working). Materialized-view inclusion of ORC data (separate, larger refactor).

**Source plan:** `~/.claude/plans/read-this-branch-sharded-nebula.md` (Plan C).

---

### Task C1: Schedule the ORC detail backfill in cron

**Files:**
- Modify: `api/src/irc_data/cli.py` (add `--backlog` flag to existing `scrape orc-detail`)
- Modify: `api/crontab.txt`

- [ ] **Step 1: Audit current orc-detail CLI.**

```bash
cd /home/irc-data/code/sailratings/api && source .venv/bin/activate
irc-data scrape orc-detail --help
```

- [ ] **Step 2: Add `--backlog` mode that processes only certs with GPH IS NULL, with `--limit`.**

```python
# api/src/irc_data/cli.py — extend existing orc-detail command
@click.option("--backlog", is_flag=True, help="Process only certs missing GPH/CDL/allowances.")
@click.option("--limit", type=int, default=500, help="Max certs per run (rate-limit-friendly).")
def scrape_orc_detail(backlog, limit, ...):
    eng = get_engine()
    if backlog:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT ref_no, country_id FROM orc_certificates
                WHERE gph IS NULL
                ORDER BY snapshot_date DESC
                LIMIT :limit
            """), {"limit": limit}).fetchall()
        for ref_no, country_id in rows:
            backfill_orc_detail(eng, ref_no, country_id)
    else:
        # existing behaviour
        ...
```

- [ ] **Step 3: Wire cron entry.**

```text
# api/crontab.txt — daily ORC detail backfill 30 min after the scrape
30 3 * * * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data scrape orc-detail --backlog --limit 500 >> /var/log/irc-data/orc-detail-$(date +\%F).log 2>&1
```

500 certs per night × 14 nights = 7,000. Clears the 7,173 backlog in ~15 days.

- [ ] **Step 4: Install crontab, run once manually to verify.**

```bash
crontab api/crontab.txt
irc-data scrape orc-detail --backlog --limit 10
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FROM orc_certificates WHERE gph IS NULL;"
```

Expect count to drop by ~10.

- [ ] **Step 5: Commit.**

```bash
git add api/src/irc_data/cli.py api/crontab.txt
git commit -m "feat(orc): schedule daily detail backfill to clear 73% missing-GPH backlog"
```

---

### Task C2: Daily match-boats after ORC scrape

**Files:**
- Modify: `api/src/irc_data/cli.py:match_boats` (add `--orc-only` flag)
- Modify: `api/crontab.txt`

- [ ] **Step 1: Add `--orc-only` flag.**

```python
# api/src/irc_data/cli.py
@click.option("--orc-only", is_flag=True, help="Skip IRC matching; only attach ORC certs to boats.")
def match_boats(orc_only, ...):
    from irc_data.matching.identity import match_orc_to_irc, match_irc_certs
    eng = get_engine()
    if not orc_only:
        match_irc_certs(eng)
    match_orc_to_irc(eng)
```

- [ ] **Step 2: Cron entry — runs 15 min after the ORC scrape.**

```text
15 3 * * * cd /home/irc-data/code/sailratings/api && source .venv/bin/activate && irc-data match-boats --orc-only >> /var/log/irc-data/match-orc-$(date +\%F).log 2>&1
```

- [ ] **Step 3: Install crontab, run once.**

```bash
crontab api/crontab.txt
irc-data match-boats --orc-only
```

- [ ] **Step 4: Confirm orphan count drops.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FROM orc_certificates WHERE boat_id IS NULL;"
```

- [ ] **Step 5: Commit.**

```bash
git add api/src/irc_data/cli.py api/crontab.txt
git commit -m "feat(orc): daily ORC-only match pass to close the 7-day orphan gap"
```

---

### Task C3: Lift `normalize_sail_tokens` into a shared module

**Files:**
- Modify: `api/src/irc_data/matching/identity.py` (currently `normalize_sail()` is naive)
- Modify: `api/src/irc_data/matching/results.py` (existing `normalize_sail_tokens` lives here)
- Test: `api/tests/test_matching_identity.py` (extend)

- [ ] **Step 1: Failing test.**

```python
# api/tests/test_matching_identity.py
import pytest
from irc_data.matching.identity import normalize_sail_tokens

@pytest.mark.parametrize("input_sail, expected", [
    ("EAUS1213", "1213"),
    ("AUS1213", "1213"),
    ("1213", "1213"),
    ("E-AUS-1213", "1213"),
    ("AUS 1213", "1213"),
])
def test_normalize_sail_tokens_strips_country_prefix(input_sail, expected):
    assert expected in normalize_sail_tokens(input_sail)

def test_normalize_sail_tokens_returns_set_for_orc_matching():
    tokens = normalize_sail_tokens("EAUS1213")
    assert "EAUS1213" in tokens
    assert "AUS1213" in tokens
    assert "1213" in tokens
```

- [ ] **Step 2: Run — expect ImportError.**

- [ ] **Step 3: Move logic.**

```python
# api/src/irc_data/matching/identity.py — add at top

COUNTRY_PREFIX_RE = re.compile(r"^[A-Z]{1,2}([A-Z]{2,3})(\d+)$")

def normalize_sail_tokens(sail: str) -> set[str]:
    """Return a set of equivalent sail-number tokens.
    E.g. EAUS1213 → {EAUS1213, AUS1213, 1213}.
    """
    if not sail:
        return set()
    s = re.sub(r"[\s\-\.]+", "", sail.upper())
    tokens = {s}
    m = COUNTRY_PREFIX_RE.match(s)
    if m:
        country, num = m.group(1), m.group(2)
        tokens.add(f"{country}{num}")
        tokens.add(num)
    if re.match(r"^[EMWJ][A-Z]{2,3}\d+$", s):
        tokens.add(s[1:])
    return tokens
```

- [ ] **Step 4: Update `match_orc_to_irc` to use it.**

```python
# api/src/irc_data/matching/identity.py — match_orc_to_irc
def match_orc_to_irc(engine):
    ...
    orc_tokens = normalize_sail_tokens(orc_cert.sail_no)
    for boat in candidate_boats:
        boat_tokens = normalize_sail_tokens(boat.sail_number)
        if orc_tokens & boat_tokens:
            return boat
    ...
```

- [ ] **Step 5: Update `results.py` to import from `identity.py` (remove the duplicate).**

```bash
grep -n "def normalize_sail_tokens" api/src/irc_data/matching/results.py
# Remove that function, add: from irc_data.matching.identity import normalize_sail_tokens
```

- [ ] **Step 6: Run tests.**

```bash
pytest api/tests/test_matching_identity.py -v
pytest api/tests/test_matching_results.py -v
```

- [ ] **Step 7: Re-run match-boats and observe.**

```bash
irc-data match-boats --orc-only
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FROM orc_certificates WHERE boat_id IS NULL;"
```

- [ ] **Step 8: Commit.**

```bash
git add api/src/irc_data/matching/identity.py api/src/irc_data/matching/results.py api/tests/test_matching_identity.py
git commit -m "refactor(matching): shared sail-token normaliser; ORC matching now matches IRC behaviour"
```

---

### Task C4: Per-cert error logging via `ingest_events`

**Files:**
- Inspect: `api/src/irc_data/db/models.py` (does `ingest_events` exist?)
- If not, create: `api/alembic/versions/0020_ingest_events.py`
- Modify: `api/src/irc_data/scrapers/orc.py`
- Modify: `api/src/irc_data/matching/identity.py`

**Coordination note:** Migration number 0020 assumes Plan A has already committed migration 0019. Before writing, check `alembic heads`; if 0020 is taken, use the next free number and update `Revises:`.

- [ ] **Step 1: Check if `ingest_events` exists.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c "\d ingest_events"
```

- [ ] **Step 2: If missing, create the migration.**

```python
# api/alembic/versions/0020_ingest_events.py
"""add ingest_events for per-cert error tracking

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table('ingest_events',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('source', sa.String(64), nullable=False, index=True),
        sa.Column('event_type', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('reference', sa.String(128), nullable=True, index=True),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('meta', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index('ix_ingest_events_source_status', 'ingest_events', ['source', 'status'])

def downgrade():
    op.drop_index('ix_ingest_events_source_status', 'ingest_events')
    op.drop_table('ingest_events')
```

- [ ] **Step 3: Add helper.**

```python
# api/src/irc_data/db/ingest_log.py (new)
import json
from sqlalchemy import text

def log_event(engine, source: str, event_type: str, status: str,
              reference: str | None, reason: str | None, meta: dict | None = None):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ingest_events (source, event_type, status, reference, reason, meta)
            VALUES (:source, :event_type, :status, :reference, :reason, :meta)
        """), {
            "source": source, "event_type": event_type, "status": status,
            "reference": reference, "reason": reason,
            "meta": meta and json.dumps(meta),
        })
```

- [ ] **Step 4: Wire logging into ORC scraper + matcher.**

```python
# api/src/irc_data/scrapers/orc.py — backfill_orc_details, on exception:
from irc_data.db.ingest_log import log_event

try:
    rms = await fetch_orc_rms(ref_no, country_id)
    upsert_orc_detail(engine, ref_no, country_id, rms)
    log_event(engine, "orc", "parse", "ok", ref_no, None)
except Exception as e:
    log_event(engine, "orc", "parse", "error", ref_no, str(e),
              meta={"country_id": country_id})

# api/src/irc_data/matching/identity.py — match_orc_to_irc, on no-match:
log_event(engine, "orc", "match", "orphan", orc_cert.ref_no,
          f"no boat match for sail={orc_cert.sail_no} name={orc_cert.yacht_name}",
          meta={"country_id": orc_cert.country_id})
```

- [ ] **Step 5: Apply migration and run.**

```bash
cd api && source .venv/bin/activate && alembic upgrade head
irc-data scrape orc-detail --backlog --limit 50
irc-data match-boats --orc-only
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT source, event_type, status, COUNT(*) FROM ingest_events GROUP BY 1,2,3 ORDER BY 1,2,3;"
```

- [ ] **Step 6: Commit.**

```bash
git add api/alembic/versions/0020_ingest_events.py api/src/irc_data/db/ingest_log.py api/src/irc_data/scrapers/orc.py api/src/irc_data/matching/identity.py
git commit -m "feat(orc): per-cert error logging + match-failure logging via ingest_events"
```

---

### Task C5: ORC diagnostic CLI reports

**Files:**
- Create: `api/src/irc_data/diagnostics/orc_reports.py`
- Modify: `api/src/irc_data/cli.py` (add `report orc-orphans` and `report orc-detail-coverage`)

- [ ] **Step 1: Build the reports module.**

```python
# api/src/irc_data/diagnostics/orc_reports.py
from sqlalchemy import text

def orphans_report(engine):
    """Per-country count of ORC certs with no boat_id, and top match-failure reasons."""
    with engine.connect() as conn:
        by_country = conn.execute(text("""
            SELECT country_id, COUNT(*) AS orphans
            FROM orc_certificates
            WHERE boat_id IS NULL
            GROUP BY country_id
            ORDER BY orphans DESC
            LIMIT 20;
        """)).fetchall()
        recent_reasons = conn.execute(text("""
            SELECT reason, COUNT(*) AS n
            FROM ingest_events
            WHERE source='orc' AND event_type='match' AND status='orphan'
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY reason
            ORDER BY n DESC
            LIMIT 10;
        """)).fetchall()
    return by_country, recent_reasons

def detail_coverage_report(engine):
    """How many ORC certs still lack GPH/CDL/allowances, by country."""
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT country_id,
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE gph IS NOT NULL) AS with_detail,
              COUNT(*) FILTER (WHERE gph IS NULL) AS missing_detail
            FROM orc_certificates
            GROUP BY country_id
            ORDER BY missing_detail DESC;
        """)).fetchall()
```

- [ ] **Step 2: Wire CLI.**

```python
# api/src/irc_data/cli.py
@cli.group("report")
def report():
    """Diagnostic reports."""

@report.command("orc-orphans")
def report_orc_orphans():
    from irc_data.diagnostics.orc_reports import orphans_report
    eng = get_engine()
    by_country, reasons = orphans_report(eng)
    click.echo("=== ORC orphans by country ===")
    for row in by_country:
        click.echo(f"  {row.country_id}: {row.orphans}")
    click.echo("\n=== Top match-failure reasons (last 7 days) ===")
    for row in reasons:
        click.echo(f"  {row.n:4d}  {row.reason}")

@report.command("orc-detail-coverage")
def report_orc_detail_coverage():
    from irc_data.diagnostics.orc_reports import detail_coverage_report
    eng = get_engine()
    rows = detail_coverage_report(eng)
    click.echo(f"{'country':10}  {'total':6}  {'with detail':12}  {'missing':8}")
    for row in rows:
        click.echo(f"{row.country_id:10}  {row.total:6d}  {row.with_detail:12d}  {row.missing_detail:8d}")
```

- [ ] **Step 3: Run.**

```bash
irc-data report orc-orphans
irc-data report orc-detail-coverage
```

- [ ] **Step 4: Commit.**

```bash
git add api/src/irc_data/diagnostics/orc_reports.py api/src/irc_data/cli.py
git commit -m "feat(orc): diagnostic CLI reports — orphans + detail coverage"
```

---

### Task C6: Verification

- [ ] **Step 1: After 14 days of daily backfill, confirm GPH-coverage target.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FILTER (WHERE gph IS NOT NULL) * 100.0 / COUNT(*) AS pct FROM orc_certificates;"
```

Target: ≥ 95% have GPH.

- [ ] **Step 2: Orphan rate target.**

```bash
psql postgresql://irc:irc@localhost:5433/irc_data -c \
  "SELECT COUNT(*) FILTER (WHERE boat_id IS NULL) * 100.0 / COUNT(*) AS pct FROM orc_certificates;"
```

Target: ≤ 10% orphan rate.

- [ ] **Step 3: Tag.**

```bash
git tag orc-correctness-v1
git push --tags
```
