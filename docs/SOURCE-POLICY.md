# SailRatings Source Policy and Responsible-Collection Gate

**Version:** v1.0
**Status:** Approved
**Approved:** 2026-09-02
**Authority:** Stuart McLeod, SailRatings founder (`stuart@sailratings.com`)
**Notion reference:** DP-01-02 (`3cc37ffe-f467-81ae-ae10-fef420851113`)
**Supersedes:** `docs/INTERIM-POLICY.md` (`interim-v0`, DP-00-01) in full — see §11
**Code of record:** `api/src/irc_data/sources/policy.py` (`CURRENT_POLICY_VERSION = "v1.0"`)
**Next review:** 2026-12-02 (quarterly), on any rights challenge, or when any
`hold` ruling in §3 is revisited

---

## 1. Purpose and Goal

**Goal: collect aggressively without ignoring law, contracts or operational harm.**

This policy governs every byte the SailRatings data platform collects from
external sources. It applies to all scrapers, source adapters, Temporal
workers, and scheduled jobs in this repository. Any collection that cannot
cite an approved source record **and** the current approved policy version is
prohibited. There is no fallback and no silent skip: the gate raises and the
collection job aborts.

---

## 2. Source Classification

Every source is classified by **how it publishes data** (`source_class`).
The class determines whether and how collection may proceed. Classification
is a first-class column on `data_sources` and is mirrored in
`CollectionPolicyDecisionV1.source_classes` in
`api/src/irc_data/sources/policy.py`.

| Class | Meaning | Collection posture |
|---|---|---|
| `public` | Freely accessible; no authentication, no paywall | Permitted when `legal_status = approved`, subject to §4 rules |
| `authenticated` | Requires an account / login | Permitted **only** with explicit written authorisation on file; credentials submitted honestly; no auth circumvention. Otherwise `blocked` |
| `licensed` | Data acquired under a licence or data-sharing agreement | Permitted only within the recorded licence scope; licence reference stored in `data_sources.licensing` |
| `prohibited` | Robots.txt blanket disallow, unauthorised login wall, paywall, or ToS forbidding automated access | No collection. Zero fetches |
| `unclear` | Rights / robots status cannot be determined from public information | Deferred pending human rights ruling; `legal_status = hold`; discovery metadata only |

The enforcement invariant (SPEC-012 §2.3) is unchanged and absolute: every
collection job must resolve its `data_sources` row before fetching. If
`legal_status != 'approved'` or `enabled = FALSE`, the gate raises
`SourceNotApprovedError` and aborts.

---

## 3. Source Rulings (v1.0)

This section **explicitly rules** on every source named in the DP-01-02
scope, including the four ToS-restricted sources and the grey-area IRC
certificate PDFs. Rulings are binding on all code in this repo and are
encoded in `policy.py` (`source_classes` / `legal_statuses`).

### 3.1 Ruling table

| Source | Class | Status @ v1.0 | Ruling (summary) |
|---|---|---|---|
| `sailsys` (SailSys) | public | **approved** | Published results; site owner has confirmed race data is open (SR-1) |
| `topyacht` (TopYacht) | public | **approved** | Club-published public results pages; see §3.3 |
| `irc-tcc` (IRC TCC listings) | public | **approved** | Published for racing administration; CSV export |
| `orc` (ORC) | public | **approved — restricted** | Public `data.orc.org` JSON API only; ToS-restricted areas excluded; see §3.3 |
| `yachtscoring` | public | **approved** | Publicly published event results |
| `manage2sail` | public | **approved** | Publicly published event results |
| `sailwave` | public | **approved** | Results files publicly linked from club sites |
| `sailing-news` | public | **approved** | RSS/Atom published for syndication |
| `irc-certs` (IRC certificate PDFs) | public (grey area) | **approved — special conditions** | See §3.4 and §6 |
| `clubspot` (ClubSpot) | unclear | **hold** | ToS restricts automated access; ruling pending; see §3.5 |
| `kwindoo` (Kwindoo) | unclear | **hold** | ToS restricts automated access; ruling pending; see §3.5 |
| any unlisted source | unclear (implicit) | **blocked** | Implicitly blocked until a source record is added and approved |

### 3.2 Interpretation

* **ORC and TopYacht are approved at v1.0** — but only for the specific
  public surfaces recorded in §3.3. Their broader ToS restrictions are
  honoured by *excluding* restricted surfaces from scope, not by ignoring
  the ToS.
* **ClubSpot and Kwindoo remain on `hold`.** Their ToS do not clearly
  permit automated collection. Until a rights ruling (or explicit
  permission) is obtained: discovery metadata (URL, title, date) only;
  **zero content fetches** to their domains.
* **IRC certificate PDFs are approved** under the special conditions of §6
  (attribution, personal-data redaction, no PDF redistribution, takedown
  path). This resolves the grey area deliberately, with mitigations,
  rather than ignoring it.

### 3.3 Ruling detail — ORC and TopYacht

**ORC (`orc`)**
* **In scope:** the public JSON API published at `data.orc.org/public/WPub.dll`
  and the public ORC event/series pages linked from `orc.org` without login.
* **Out of scope:** any ORC surface requiring an account, any area whose
  ToS or robots.txt forbids automated access, and any bulk export not
  explicitly published for automated consumption.
* **Posture:** collect aggressively from the public API; honour robots.txt
  and rate rules (§4) without exception. If ORC tightens its robots.txt or
  ToS, the affected paths are disabled via §7 immediately and the incident
  is logged.

**TopYacht (`topyacht`)**
* **In scope:** public race-results pages published by clubs on
  `topyacht.net.au` without authentication.
* **Out of scope:** admin/login areas, any club-restricted series, any path
  disallowed by robots.txt.
* **Posture:** public HTML results only, nightly window, per-domain rate
  limit. TopYacht's platform ToS is respected by collecting only what clubs
  publish publicly and by honouring the takedown path in §5 without delay.

### 3.4 Ruling detail — IRC certificate PDFs (grey area)

IRC certificates are published for racing administration and are publicly
accessible without authentication, but IRC does not publish an explicit
machine-use licence. That is the grey area. The ruling at v1.0:

* **Approved for collection**, because certificate measurements/ratings are
  the core datum of this platform and cannot be obtained at the required
  granularity from any other source.
* **Approved with the special conditions of §6** (attribution header,
  personal-data redaction, no raw-PDF redistribution, immediate takedown
  path) so the residual legal/relationship risk is actively mitigated
  rather than ignored.
* Re-review trigger: any contact from the IRC Rating Office, any robots.txt
  change on `ircrating.org`, or the next quarterly review — whichever first.

### 3.5 Ruling detail — ClubSpot and Kwindoo (ToS-restricted)

* Both platforms' terms restrict automated access/scraping.
* Ruling: `legal_status = hold`, `source_class = unclear`.
* Behaviour: log discovery metadata only; **zero HTTP fetches** to their
  domains during content collection; review quarterly (next: 2026-12-02) or
  immediately upon obtaining written permission.

### 3.6 Adding a new source

Any source not in §3.1 is implicitly `blocked`. To add one:

1. Create a `data_sources` row with `legal_status = 'hold'` and a
   `source_class` classification.
2. Obtain a rights ruling (internal review or external counsel).
3. Update to `legal_status = 'approved'` with policy version `v1.0` and the
   approval date in `notes`.

---

## 4. Responsible-Collection Rules (non-negotiable)

These rules apply to **all** collection regardless of source approval. They
are enforced in code by `CollectionGate`, `HttpClient`, and the acquisition
primitives, and are the v1.0 (unchanged-in-substance) codification of
interim-v0 §3.

### 4.1 robots.txt
* Fetch and parse `robots.txt` at the start of every collection session.
* Cache the parsed disallow list in `data_sources.robots_disallow`.
* Re-fetch if the cached value is older than 24 hours.
* Skip any path matching a disallow rule for our User-Agent or `*`.
* A 404 on robots.txt = no disallow rules; proceed normally.
* If robots.txt cannot be fetched (5xx / network error): **stop collection
  for that source** and create a `source_incident`.

### 4.2 Rate limiting
* Maximum **1 request per 2 seconds** per domain.
* Jitter: actual delay = 2.0s + random(0, 1.0s).
* Honour `Retry-After` on 429 responses.
* Exponential backoff on repeated 5xx: 2s → 4s → 8s → 16s → abort.

### 4.3 Collection window
* Nightly only: **01:00–06:00 source-local time** where known; otherwise UTC
  01:00–06:00.
* No daytime scraping except single-URL on-demand health checks.
* Exception: SailSys results every 30 min (lightweight published feed).

### 4.4 Conditional requests
* Always send `If-None-Match` (last ETag) and `If-Modified-Since` on repeat
  fetches.
* Treat `304 Not Modified` as a clean success — do not re-download or
  re-store; record `status = 'not_modified'`.

### 4.5 Content deduplication and retention
* SHA-256 every response body before storage.
* Hash match with last stored artifact for that URL → skip storage, log
  `status = 'duplicate'`.
* Store hashes in the raw-artifact metadata alongside the artifact.
* Raw artifact retention: 365 days (`RetentionRule.raw_artifact_retention_days`).

### 4.6 Hard caps per source per night
* Max **25 MB** per individual object; reject and log larger files.
* Max **5,000 HTTP fetches** per source per nightly run; stop and log.
* Max **500 MB** total download per source per night; stop and log.

### 4.7 Prohibited collection
* No login walls — do not submit credentials to access gated content without
  written authorisation.
* No paywalls — do not circumvent subscription or payment gates.
* No CAPTCHA bypass — no solvers, proxies, or human relay.
* No personal data beyond published results — no harvesting email, phone,
  address, or financial data (see §4.8).
* No session hijacking, token reuse, or auth-header manipulation.

### 4.8 Personal data
* Published race results (boat name, sail number, rating, finishing
  position) may be collected.
* Prohibited fields: owner name, owner email, owner phone, owner address,
  home port where it implies an individual's address, any financial data.
  (Encoded in `PersonalDataRule.prohibited_fields`.)

### 4.9 Attribution
* Every HTTP request carries the standard User-Agent (§8).
* Every artifact references its `data_sources` source record.
* `irc-certs` requests additionally carry `X-SailRatings-Source: irc-certs`.

---

## 5. Takedown and Complaint Response

If a source operator requests removal of their data:

1. **Within 4 hours:** set `data_sources.enabled = FALSE` for that slug (the
   kill switch) — no further fetches.
2. Move all captured artifacts for that source to `data/raw/quarantine/<slug>/`.
3. Acknowledge to the operator, confirming collection has stopped.
4. **Within 48 hours:** assess whether derived data must be removed.
5. Log in `source_incidents` with `incident_type = 'takedown_request'`.

Takedown contact: `stuart@sailratings.com`.

---

## 6. IRC Certificate PDFs — Specific Guidance (carried forward)

**Status:** Approved, v1.0, 2026-09-02 (grey-area ruling, §3.4)

* Attribution: send `X-SailRatings-Source: irc-certs` with every request.
* Source slug: every artifact MUST reference the `irc-certs` source record.
* **No re-distribution of raw PDFs** — derived data only in the public API.
* Store raw PDFs in `data/raw/certs/` (not in git, not on the API).
* Parse and store: sail number, TCC, measurement fields, certificate date,
  expiry.
* Do **NOT** store: owner names, owner contact details, home port if it
  implies an individual's address.
* Takedown path: §5.

---

## 7. Kill Switch / Emergency Disable

The emergency disable works on **two independent dimensions** and both are
enforced by `CollectionGate` on every resolution and every URL check:

* **By source:** `data_sources.enabled = FALSE`, or runtime
  `CollectionGate.emergency_disable_source(slug)`. Takes effect on the next
  resolve — no DB round-trip needed for the runtime form.
* **By domain:** `CollectionGate.emergency_disable_domain(domain)` (persisted
  in `domain_disables`). Blocks the domain **and all its subdomains**
  immediately, across every source that might touch it.
* **Global:** `COLLECTION_ENABLED=false` halts all collection across all
  sources without DB changes.

Re-enable only after written approval from Stuart McLeod. Quarantine existing
captures to `data/raw/quarantine/<slug>/`.

---

## 8. User-Agent

All HTTP requests from this platform MUST use:

```
SailRatings/1.0 (+https://sailratings.com; contact=stuart@sailratings.com)
```

Set as the default in `get_http_client()` / `HttpClient`. Never override
with a browser User-Agent or a blank User-Agent.

---

## 9. Source Monitor

Nightly, for each approved source:
* Fetch the canonical index/landing page; compare SHA-256 to the stored hash.
* Hash changed ≥5% or structure change → quarantine that source's nightly
  output and create a `source_incident` (`+24h` default quarantine; manual
  review to clear).
* robots.txt changed → re-parse; if new disallow paths conflict with
  existing collection paths, create an incident and disable affected paths.

---

## 10. Policy Versioning and Enforcement

The current approved policy version is **`v1.0`**
(`CURRENT_POLICY_VERSION` in `api/src/irc_data/sources/policy.py`).

**The adapter cannot run without the approved policy version.** Every
`data_sources` row carries `policy_version`; every collection job asserts:

```python
if source.policy_version != CURRENT_POLICY_VERSION:
    raise PolicyVersionMismatchError(...)
```

On any revision of this document:
1. Update this file with a new version string and a §12 changelog entry.
2. Update `CURRENT_POLICY_VERSION` in `policy.py` and the classification
   tables.
3. Stamp `policy_version` on all affected `data_sources` rows via an Alembic
   migration.
4. Re-run the policy verification suite
   (`pytest tests/sources/test_policy.py`).

---

## 11. Supersession of interim-v0 (DP-00-01)

This v1.0 policy **supersedes `docs/INTERIM-POLICY.md` (`interim-v0`) on
approval (2026-09-02)**.

* `INTERIM-POLICY.md` is retained for historical audit only and is no longer
  normative.
* Substantive rules are carried forward unchanged; v1.0 adds the explicit
  §3 rulings on the ToS-restricted sources and the grey-area IRC PDFs, and
  makes the emergency-disable dimensionality (source **and** domain) a
  first-class acceptance-criteria guarantee.
* All `data_sources` rows are stamped `policy_version = 'v1.0'` by
  migration `0026_policy_v1_rulings`; rows still referencing `interim-v0`
  fail the policy gate until stamped.

---

## 12. Changelog

| Version | Date | Author | Summary |
|---|---|---|---|
| interim-v0 | 2026-08-30 | Stuart McLeod | Initial policy; 9 approved sources, 2 on hold; IRC PDFs approved; SR-1 decisions baked in |
| v1.0 | 2026-09-02 | Stuart McLeod | Supersedes interim-v0. Explicit rulings: ORC (public API only), TopYacht (public results), ClubSpot & Kwindoo (hold), IRC cert PDFs (approved with special conditions). Source & domain emergency disable formalised. |
