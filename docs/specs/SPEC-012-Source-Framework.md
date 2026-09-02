# SPEC-012: Data Platform Source Framework

**Covers:** DP-01-01 through DP-01-05  
**Policy authority:** `docs/SOURCE-POLICY.md` (v1.0, approved 2026-09-02; supersedes `docs/INTERIM-POLICY.md` interim-v0)  
**Supersedes:** SPEC-13 §2–§3 (operational cadence and rate-limiting now governed here)

---

## 1. Purpose

Replace the current collection of 15+ bespoke scrapers with a governed,
observable source framework: a source registry, a shared adapter SDK, common
acquisition primitives, and a change monitor. Every byte the platform collects
must reference an approved source record and a policy version.

---

## 2. Source Register (DP-01-01)

### 2.1 Database table: `data_sources`

```sql
CREATE TABLE data_sources (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,          -- e.g. 'sailsys', 'irc-certs'
    display_name    TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    category        TEXT NOT NULL,                 -- 'results', 'ratings', 'certificates', 'news'
    adapter_class   TEXT,                          -- dotted Python path
    policy_version  TEXT NOT NULL DEFAULT 'v1.0',
    legal_status    TEXT NOT NULL DEFAULT 'approved',
                                                   -- 'approved' | 'hold' | 'blocked'
    robots_checked_at TIMESTAMPTZ,
    robots_disallow  TEXT[],                       -- cached disallow paths
    contact_email   TEXT,
    notes           TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2.2 Seed entries (v1.0 approved sources)

| slug | display_name | category | legal_status |
|---|---|---|---|
| `sailsys` | SailSys | results | approved |
| `topyacht` | TopYacht | results | approved |
| `irc-tcc` | IRC TCC Listings | ratings | approved |
| `orc` | ORC | ratings | approved |
| `yachtscoring` | Yacht Scoring | results | approved |
| `manage2sail` | Manage2Sail | results | approved |
| `sailwave` | Sailwave | results | approved |
| `sailing-news` | Sailing News Feeds | news | approved |
| `irc-certs` | IRC Certificate PDFs | certificates | approved |
| `clubspot` | ClubSpot | results | hold |
| `kwindoo` | Kwindoo | results | hold |

`hold` sources: discovery metadata only, no content capture, until DP-01-02
policy enforcement is live and a rights ruling has been obtained.

### 2.3 Enforcement invariant

Every collection job MUST resolve a `data_sources` row before fetching. If
`legal_status != 'approved'` or `enabled = FALSE`, raise
`SourceNotApprovedError` and abort. No fallback, no silent skip.

### 2.4 Alembic migration

Add migration `0008_data_sources.py`. Include index on `slug` and a check
constraint ensuring `legal_status IN ('approved', 'hold', 'blocked')`.

---

## 3. Responsible Collection Policy (DP-01-02)

Full policy text lives in `docs/SOURCE-POLICY.md` (v1.0). The code enforces it.

### 3.1 Policy version gate

```python
CURRENT_POLICY_VERSION = "v1.0"

def assert_policy_current(source: DataSource):
    if source.policy_version != CURRENT_POLICY_VERSION:
        raise PolicyVersionMismatchError(
            f"{source.slug} references {source.policy_version}, "
            f"current is {CURRENT_POLICY_VERSION}"
        )
```

Every adapter calls `assert_policy_current(source)` before the first fetch.

### 3.2 Politeness rules (non-negotiable)

- **robots.txt**: fetch and cache at session start; skip any disallowed path.
- **Rate**: max 1 request per 2 seconds per domain; enforce via `RateLimiter`.
- **Window**: nightly only, 01:00–06:00 source-local time where known.
- **Conditional requests**: send `If-None-Match` / `If-Modified-Since`; treat
  304 as a clean success — do not re-download unchanged content.
- **Content hash**: SHA-256 every response body before storing; skip if hash
  matches the last stored artifact for that URL.
- **Hard caps per source per night**: max object size 25 MB; max 5 000 fetches.
- **No auth circumvention**: no login walls, no paywalls, no CAPTCHA bypass,
  no personal-data harvesting beyond published results.

### 3.3 User-Agent

```
SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)
```

Set as the default `User-Agent` in `get_http_client()` in `scrapers/base.py`.

### 3.4 Kill switch

`data_sources.enabled` is the kill switch. A per-source disable is effective
immediately (checked before every fetch cycle). Complaint or takedown request →
set `enabled = FALSE`, quarantine existing captures for that source
(move to `raw_artifacts_quarantine/`).

### 3.5 IRC Certificate PDFs — specific guidance

Status: **approved** (v1.0, 2026-09-02; originally interim-v0, 2026-08-30).  
Rationale: published for racing administration, publicly accessible, core
platform data. Capture with:
- Attribution header: `X-SailRatings-Source: irc-certs`
- Takedown path documented in `docs/INTERIM-POLICY.md §5`
- No re-distribution of raw PDFs; derived data only in the public API

---

## 4. Source Adapter SDK (DP-01-03)

### 4.1 Abstract base

```python
# src/irc_data/sources/adapter.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

@dataclass
class FetchResult:
    url: str
    content: bytes
    content_hash: str        # SHA-256 hex
    etag: str | None
    last_modified: str | None
    fetched_at: str          # ISO-8601
    policy_version: str

class SourceAdapter(ABC):
    """All source adapters inherit from this. Enforces policy + politeness."""

    source_slug: str         # must match data_sources.slug

    def __init__(self, db, http_client):
        self.db = db
        self.http = http_client
        self._source = self._resolve_source()   # raises if not approved

    def _resolve_source(self):
        src = get_source(self.db, self.source_slug)
        assert_policy_current(src)              # raises PolicyVersionMismatchError
        if not src.enabled:
            raise SourceNotApprovedError(self.source_slug)
        return src

    @abstractmethod
    async def collect(self) -> AsyncIterator[FetchResult]:
        """Yield raw FetchResult objects. No parsing, no side effects."""
        ...

    async def run(self) -> list[FetchResult]:
        results = []
        async for r in self.collect():
            results.append(r)
        return results
```

### 4.2 Reference adapter: `FakeSourceAdapter`

A `FakeSourceAdapter` against a local HTTP server proves:
- Pagination (multi-page result sets)
- Retry on transient 5xx
- Checkpoint resume (if interrupted mid-collection, resume from last page)
- Content hashing (skip re-download of unchanged pages)
- Policy enforcement (raises if source is `hold`)

Ship the fake adapter and its tests alongside the SDK. Tests must pass
`pytest tests/sources/test_fake_adapter.py -v` with no network calls.

---

## 5. Acquisition Primitive Library (DP-01-04)

Extend `scrapers/base.py` (or create `sources/primitives.py`) with:

| Primitive | Purpose |
|---|---|
| `fetch_html(url)` | GET with rate-limit, retry, conditional request, hash check |
| `fetch_pdf(url)` | Same + enforces 25 MB cap; returns `FetchResult` |
| `fetch_json(url)` | Same + validates Content-Type |
| `fetch_file(url)` | Generic binary fetch (Sailwave `.blw` files etc.) |
| `paginate(seed_url, next_fn)` | Async generator: follows pagination until exhausted or cap hit |
| `render_page(url)` | Playwright headless fetch for JS-rendered sources; returns HTML + screenshot evidence |

All primitives:
- Accept an optional `source: DataSource` arg; call `assert_policy_current` if provided
- Respect `robots_disallow` from the source record
- Set the standard `User-Agent`
- Return `FetchResult` (never raw bytes directly)

---

## 6. Source Monitor (DP-01-05)

### 6.1 What it watches

For each approved `data_sources` row, nightly:
1. Fetch the source's canonical index/landing page
2. SHA-256 hash the response
3. Compare to the previous stored hash

### 6.2 Change classification

| Delta | Action |
|---|---|
| Hash unchanged | No-op; log "clean" |
| Hash changed < 5% content diff | Log "minor change"; continue collection |
| Hash changed ≥ 5% OR structure change | Create a `source_incident` record; **quarantine that source's nightly output** (do not publish); alert via health-check webhook |
| robots.txt changed | Re-parse; if new disallow paths conflict with existing collection, create incident and disable affected paths |

### 6.3 Database table: `source_incidents`

```sql
CREATE TABLE source_incidents (
    id              SERIAL PRIMARY KEY,
    source_slug     TEXT NOT NULL REFERENCES data_sources(slug),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    incident_type   TEXT NOT NULL,   -- 'structure_change' | 'robots_change' | 'hash_delta'
    previous_hash   TEXT,
    current_hash    TEXT,
    artifact_url    TEXT,
    resolved_at     TIMESTAMPTZ,
    notes           TEXT
);
```

### 6.4 Quarantine

When an incident is created, set a `quarantine_until` timestamp on the
source record (default: `+24h`). The nightly scraper checks this before
running and skips quarantined sources.

---

## 7. Acceptance Criteria (full DP-01)

- [ ] `data_sources` table exists with all 11 seed rows; Alembic migration clean
- [ ] `SourceNotApprovedError` raised for any `hold` or `disabled` source
- [ ] `PolicyVersionMismatchError` raised if source policy_version != `CURRENT_POLICY_VERSION`
- [ ] `RateLimiter` enforces ≤ 1 req/2s per domain with jitter
- [ ] `get_http_client()` sets standard `User-Agent` by default
- [ ] `FakeSourceAdapter` tests pass with zero network calls
- [ ] All six acquisition primitives exist and return `FetchResult`
- [ ] `render_page()` uses Playwright and returns HTML + screenshot path
- [ ] Source monitor runs nightly; creates `source_incidents` on material change
- [ ] `source_incidents` table and quarantine logic present; Alembic migration clean
- [ ] IRC cert collection uses `irc-certs` source record, policy approved
- [ ] `hold` sources (ClubSpot, Kwindoo) produce zero fetch attempts

---

## 8. Out of scope for DP-01

- Parsing / normalisation of raw artifacts → DP-02 / DP-03
- Identity resolution → DP-04
- Publishing derived data via the API → existing routers unchanged
