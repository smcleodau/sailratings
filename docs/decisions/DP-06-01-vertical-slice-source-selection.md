# DP-06-01 — First Source Vertical-Slice Selection (IRC + ORC pair)

| | |
|---|---|
| **Issue** | DP-06-01 |
| **Status** | **DECIDED — submitted for human verification** |
| **Date** | 2026-09-02 |
| **Policy of record** | `docs/SOURCE-POLICY.md` **v1.0** (approved 2026-09-02, DP-01-02) |
| **Register of record** | `data_sources` (DP-01-01), seed `api/src/irc_data/sources/seed_data.py` |
| **Blocked by** | DP-01-01 (governed register) ✅ landed · DP-01-02 (rights rulings) ✅ landed |
| **Verification** | `api/scripts/verify_dp_06_01.py` (evidence generator, live + offline) |

> **One-line decision:** the first vertical slice is the **IRC + ORC
> certificate pair** sourced from **`irc-tcc` + `irc-certs` (IRC leg)** and
> **`orc` via its public `data.orc.org` JSON API (ORC leg)**, with **`sailsys`
> as the results platform feeding RAI**. Backup: **`topyacht`** for the
> results/RAI leg.

---

## 1. The decision

**DECISION MADE (per ticket): IRC and ORC from the start.** This ticket selects
the concrete source pair that exercises the architecture end-to-end and
delivers visible value.

### Selected vertical slice (the named source pair)

| Leg | Source slug | Surface | Register ruling (DP-01-01) | Policy ruling (DP-01-02 §3) |
|---|---|---|---|---|
| **IRC certificate source** | `irc-tcc` | IRC TCC online listings (CSV export) | `approved` / `active` / `public_domain` | §3.1 approved — published for racing administration |
| **IRC certificate source (depth)** | `irc-certs` | IRC certificate PDFs (parsed, derived data only) | `approved` / `prototyped` / `grey_area` | §3.4 approved with special conditions (§6) |
| **ORC certificate source** | `orc` | **public `data.orc.org/public/WPub.dll` JSON API only** | `approved` / `active` / `tos_restricted` | §3.3 approved-**restricted** — public API only, ToS-restricted surfaces excluded |
| **Results platform for RAI** | `sailsys` | Published race-results feed | `approved` / `active` / `licensed_api` | §3.1 approved — owner confirmed race data is open (SR-1) |

### Backup (one)

| Role | Source slug | Why it is the backup |
|---|---|---|
| Backup results platform for RAI | `topyacht` | Same entity surface (`Event`/`EventEntry`/`RaceResult`), already `approved`/`active`, 3,353 results already captured. TOS-restricted → **public club-published results pages only** (§3.3). Swap-in if SailSys feed degrades or its open-data confirmation (SR-1) changes. |

### Approved policy under which this slice collects

**`docs/SOURCE-POLICY.md` v1.0** (2026-09-02). The ORC dependency called out
in the ticket is resolved by DP-01-02's explicit §3.3 ruling: of the three
options on the table (published certificate search with attribution + takedown;
ORC data licence; owner-uploaded certificates), **the approved ORC path is the
public `data.orc.org` JSON API with attribution, rate limits and the §5
takedown process.** No ORC licence has been signed and no owner-upload path is
required for this slice; both remain future options and do not block.

---

## 2. Why this pair exercises the architecture

The goal is a source choice that touches every plane of the platform, not the
cheapest source. This pair is deliberately the widest exercise available:

| Architecture concern | How this slice exercises it |
|---|---|
| **Policy gate (SPEC-012 §2.3)** | 4 sources × 3 distinct rulings — `public_domain`, `grey_area` special-conditions (§6), `tos_restricted` scope-limited (§3.3). Every collection job must pass `resolve_source()` under v1.0. |
| **Acquisition primitives** | CSV scrape (`irc-tcc`), PDF download+parse (`irc-certs`), JSON API (`orc`), lightweight 30-min feed (`sailsys`). Four different `access_method`s / `ContentType`s. |
| **Provenance / raw lake (DP-02)** | Each leg emits raw artifacts + provenance envelopes; PDF, JSON, CSV, HTML feed all content-addressed. |
| **Identity resolution (DP-04)** | One `Boat` must be matched across IRC TCC, IRC certs, ORC certs and SailSys results — the hardest join the platform does. |
| **Data quality (DP-05)** | All four published datasets already have DQ rules: `tcc_listing`, `irc_certificates`, `orc_register`, `race_results` (§4 of `data-quality/dimensions.md`). |
| **Scheduling (OPS-01-01)** | Nightly-window sources (IRC/ORC/TopYacht) **plus** the only 30-min cadence source (SailSys) — exercises both cadence classes. |
| **Scoring / analytics (RAI)** | RAI needs rating + results on the same boat; only this pair supplies both IRC TCC *and* ORC APH/CDL on boats that also have race results. |

## 3. Why it delivers visible value

The vertical slice must end in something a sailor can see. This pair powers the
two flagship user-visible outputs:

* **Boat page / report** — IRC TCC + ORC ratings side-by-side, certificate
  history, and the **RAI score card** (§ `analysis/performance.compute_rai`).
* **Fleet / rivals table** — requires a results platform (SailSys) joined to
  both rating systems, which only this combination yields.

## 4. Evidence — DP-00 capture volumes (direct evidence for the choice)

Measured live from the platform database (`irc_data` @ 2026-09-02). These are
the DP-00 capture volumes the ticket cites as direct evidence:

| Signal | Volume |
|---|---|
| IRC TCC snapshots | **36,932** |
| IRC certificate PDFs parsed | **3,809** |
| ORC certificates | **19,865** |
| Race results — **SailSys** | **212,857** (2018-09 → 2026-07), 3,636 ingestion runs |
| Race results — TopYacht (backup) | 3,353 |
| Distinct boats with results (RAI-eligible) | 5,686 |
| **SailSys boats that also have an IRC TCC snapshot** | **967** |
| **SailSys boats that also have an ORC certificate** | **733** |
| **Boats present in BOTH IRC and ORC registers** | **1,372** |
| SailSys 30-min feed freshness | 122 runs in last 7 d, latest 2026-09-02 22:06 UTC |

The join rows (967 / 733 / 1,372) are the load-bearing evidence: they prove
the slice produces boats that have **rating + results + identity** together —
the exact intersection the RAI engine and the report need. No other candidate
pair comes close on both coverage and policy clearance.

## 5. ORC options considered (ticket-mandated, per DP-01-02)

| Option | Ruling | Disposition |
|---|---|---|
| **(a) ORC published certificate search** with attribution + takedown | **SELECTED** — narrowed by DP-01-02 §3.3 to the public `data.orc.org` JSON API only; attribution UA + §5 takedown apply; restricted/ToS areas excluded | In slice |
| (b) ORC data licence | Not required for the public API; pursue in parallel for bulk/premium data | Future, non-blocking |
| (c) Owner-uploaded certificates | No ingestion path yet; adds UX + moderation scope | Future, non-blocking |

## 6. Success metrics (slice exit criteria)

Baselines from §4; targets are the bar the vertical slice must hit to be called
a success.

| # | Metric | Baseline (2026-09-02) | Slice target |
|---|---|---|---|
| M1 | Boats with IRC TCC + ≥1 SailSys result (RAI-eligible) | 967 | ≥ 1,500 |
| M2 | Boats with ORC cert + ≥1 SailSys result | 733 | ≥ 1,100 |
| M3 | Boats in both IRC & ORC registers (dual-rated) | 1,372 | ≥ 2,000 |
| M4 | Slice boats with a computed RAI | 0 (not wired to slice) | ≥ 500 |
| M5 | Identity match precision on the 3-way join (manual sample) | n/a | ≥ 97% |
| M6 | Provenance coverage on slice published rows (DP-05) | 100% target | 100% (blocking) |
| M7 | Collection policy violations / takedown incidents | 0 | 0 |
| M8 | SailSys feed staleness (sched-v1.0 budget) | within 30-min cadence | within staleness budget ≥ 99% of nights |

## 7. Guardrails (in force for every fetch in this slice)

* robots.txt fail-closed; 1 req / 2 s + jitter; nightly 01:00–06:00 window
  (SailSys 30-min feed is the sanctioned exception).
* `irc-certs`: attribution header `X-SailRatings-Source: irc-certs`, **no raw
  PDF redistribution**, owner personal data not stored, §5 takedown.
* `orc`: public `data.orc.org` JSON API **only**; any login/ToS-restricted
  surface is out of scope; robots/ToS tightening → immediate §7 disable.
* Kill switch armed per-source and per-domain (§7); global `COLLECTION_ENABLED`.

## 8. What this decision explicitly does NOT do

* Does not scrape ClubSpot/Kwindoo (still `hold`, discovery metadata only).
* Does not touch any ORC authenticated/ToS-restricted surface.
* Does not sign an ORC licence or build owner-upload — both remain open options.

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-09-02 | Lane Worker Agent (for Stuart McLeod) | Initial decision record; selected IRC TCC+certs / ORC public API / SailSys; backup TopYacht. |
