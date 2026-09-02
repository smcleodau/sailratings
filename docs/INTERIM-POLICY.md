# SailRatings Interim Data Collection Policy

> **SUPERSEDED — historical reference only.**
> This document was superseded on 2026-09-02 by
> [`docs/SOURCE-POLICY.md`](SOURCE-POLICY.md) **v1.0** (DP-01-02,
> Notion `3cc37ffe-f467-81ae-ae10-fef420851113`). The current approved
> policy version is `v1.0` (`CURRENT_POLICY_VERSION` in
> `api/src/irc_data/sources/policy.py`). Do not cite `interim-v0` for new
> collection; source rows referencing `interim-v0` fail the policy gate.

**Version:** interim-v0  
**Approved:** 2026-08-30  
**Authority:** Stuart McLeod, SailRatings founder  
**Notion reference:** SR-1 (`3c737ffe-f467-81cf-808d-ea584e29555e`)  
**Next review:** When DP-02 (normalisation) goes live or on any rights challenge  

---

## 1. Purpose

This policy governs every byte the SailRatings data platform collects from
external sources. It applies to all scrapers, adapters, workers, and automated
jobs in this repository. Any collection that cannot cite an approved source
record and a policy version is prohibited.

---

## 2. Approved Sources (interim-v0)

The following sources are approved for content collection under this policy.
All others are implicitly blocked until a source record is added and approved.

### 2.1 Fully approved — content capture enabled

| Source slug | Name | Category | Rationale |
|---|---|---|---|
| `sailsys` | SailSys | results | Australian race management; publicly published results |
| `topyacht` | TopYacht | results | Australian race management; publicly published results |
| `irc-tcc` | IRC TCC Listings | ratings | Published for racing administration; CSV download from ircrating.org |
| `orc` | ORC | ratings | Published for racing administration; JSON API from data.orc.org |
| `yachtscoring` | Yacht Scoring | results | US/international race results; publicly published |
| `manage2sail` | Manage2Sail | results | European race management; publicly published results |
| `sailwave` | Sailwave | results | Results files publicly linked from club sites |
| `sailing-news` | Sailing News Feeds | news | RSS/Atom feeds; explicitly published for syndication |
| `irc-certs` | IRC Certificate PDFs | certificates | Approved (see §4); publicly accessible; core platform data |

### 2.2 On hold — discovery metadata only

| Source slug | Name | Reason for hold |
|---|---|---|
| `clubspot` | ClubSpot | Rights ruling pending; ToS review incomplete |
| `kwindoo` | Kwindoo | Rights ruling pending; ToS review incomplete |

Hold sources: log metadata (URL, title, date) only. **No content capture.**
Zero HTTP fetches to hold source domains during content collection windows.
Review quarterly.

### 2.3 Blocked

Any source not listed above is implicitly `blocked`. To add a source:
1. Create a `data_sources` row with `legal_status = 'hold'`
2. Obtain a rights ruling (internal review or external counsel)
3. Update to `legal_status = 'approved'` with policy version and approval date in notes

---

## 3. Responsible Collection Rules (non-negotiable)

These rules apply to all collection, regardless of source approval status.

### 3.1 robots.txt compliance

- Fetch and parse `robots.txt` at the start of every collection session
- Cache the parsed disallow list in `data_sources.robots_disallow`
- Re-fetch robots.txt if cached value is older than 24 hours
- Skip any URL path that matches a disallow rule for our User-Agent or `*`
- A 404 on robots.txt = no disallow rules; proceed normally
- If robots.txt cannot be fetched (5xx, network error): **stop collection for that source** and create a `source_incident`

### 3.2 Rate limiting

- Maximum **1 request per 2 seconds** per domain
- Apply jitter: actual delay = 2.0s + random(0, 1.0s)
- Honour `Retry-After` headers on 429 responses
- Back off exponentially on repeated 5xx: 2s → 4s → 8s → 16s → abort

### 3.3 Collection window

- Nightly only: **01:00–06:00 source-local time** where timezone is known
- For sources with unknown timezone: use UTC 01:00–06:00
- No daytime scraping except for on-demand health checks (single URL, no bulk)
- Exception: SailSys results every 30 min (lightweight, published results feed)

### 3.4 Conditional requests

- Always send `If-None-Match` with the last known ETag on repeat fetches
- Always send `If-Modified-Since` with the last fetch timestamp
- Treat 304 Not Modified as a clean success — do not re-download, do not re-store
- Record the 304 in `ingestion_log` with `status = 'not_modified'`

### 3.5 Content deduplication

- SHA-256 hash every response body before storage
- If the hash matches the last stored artifact for that URL: skip storage, log `status = 'duplicate'`
- Store hashes in `raw_artifacts` table alongside the artifact metadata

### 3.6 Hard caps per source per night

- Maximum **25 MB** per individual downloaded object; reject and log larger files
- Maximum **5,000 HTTP fetches** per source per nightly run; stop and log if hit
- Maximum **500 MB** total download per source per night; stop and log if hit

### 3.7 Prohibited collection

- No login walls — do not submit credentials to access gated content
- No paywalls — do not circumvent subscription or payment gates
- No CAPTCHA bypass — do not use solvers, proxies, or human relay for CAPTCHAs
- No personal data beyond published results — do not harvest email, phone, address, or financial data
- No session hijacking, token reuse, or auth header manipulation

---

## 4. IRC Certificate PDFs — Specific Guidance

**Status:** Approved, interim-v0, 2026-08-30

**Rationale:** IRC certificates are published for racing administration. They are
publicly accessible from the IRC website without authentication. Certificate
data (measurements, ratings) is the core datum of this platform and cannot
be obtained from any other source at the required granularity.

**Collection rules (in addition to §3):**

- Attribution: send `X-SailRatings-Source: irc-certs` header with every request
- Source slug: every artifact MUST reference `irc-certs` source record
- No re-distribution of raw PDFs — derived data only in the public API
- Store raw PDFs in `data/raw/certs/` (not in git, not on the API)
- Parse and store: sail number, TCC, measurement fields, certificate date, expiry
- Do NOT store: owner names, owner contact details, home port if it implies an individual's address

**Takedown path:** See §5.

---

## 5. Takedown and Complaint Response

If a source operator contacts us requesting removal of their data:

1. **Immediate** (within 4 hours of contact): set `data_sources.enabled = FALSE` for that slug
2. This triggers the kill switch — no further fetches from that source
3. Move all captured artifacts for that source to `data/raw/quarantine/<slug>/`
4. Respond to the operator acknowledging receipt and confirming collection has stopped
5. Within 48 hours: assess whether derived data (parsed fields in DB) must be removed
6. Log the incident in `source_incidents` with `incident_type = 'takedown_request'`

Contact for takedown requests: `stuart@sailratings.com`

---

## 6. User-Agent

All HTTP requests from this platform MUST use:

```
SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)
```

Set as the default `User-Agent` in `get_http_client()` in `scrapers/base.py`.
Never override with a browser User-Agent or blank User-Agent.

---

## 7. Kill Switch

`data_sources.enabled` is the per-source kill switch.

- `enabled = FALSE`: no fetches, no collection jobs, no content storage
- Checked before every fetch cycle at the workflow level
- Complaint or takedown → set `enabled = FALSE` first, investigate second
- Quarantine existing captures: move to `data/raw/quarantine/<slug>/`
- Re-enable only after written approval from Stuart McLeod

Global kill switch: set `COLLECTION_ENABLED=false` environment variable to
halt all collection across all sources without DB changes.

---

## 8. Source Monitor

A nightly monitor checks each approved source for structural changes:

- Fetch the canonical index/landing page for each source
- Compare SHA-256 hash to previous stored hash
- If hash changed ≥5% OR structure changed: quarantine that source's nightly output and create a `source_incident`
- If robots.txt changed: re-parse; if new disallow paths conflict with existing collection paths, create incident and disable affected paths
- Quarantine period: `+24h` default; manual review required to clear

---

## 9. Policy Version

This document is `interim-v0`. Every `data_sources` row carries a `policy_version`
field. Every collection job asserts:

```python
if source.policy_version != CURRENT_POLICY_VERSION:
    raise PolicyVersionMismatchError(...)
```

When this policy is revised:
1. Update this file with a new version string (e.g., `interim-v1`)
2. Update `CURRENT_POLICY_VERSION` in `src/irc_data/sources/adapter.py`
3. Update `policy_version` on all affected `data_sources` rows via migration
4. Document the change here under a new §10 "Changelog" section

---

## 10. Changelog

| Version | Date | Author | Summary |
|---|---|---|---|
| interim-v0 | 2026-08-30 | Stuart McLeod | Initial policy; 9 approved sources, 2 on hold; IRC PDFs approved; SR-1 decisions baked in |
