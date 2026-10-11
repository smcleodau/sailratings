# SPEC-25: Intelligence Query Service

Status: DRAFT — awaiting Human Gate (Stuart approval of §1)
Related: SPEC-11 (Sailing Intelligence Reports), SPEC-10 (Subscriptions), SPEC-17 (AI Product Manager)
Section owners: §1 IN-01-01 · §2 IN-01-02 · §3 IN-01-03 · §4 IN-01-05

## 1. Decision brief (ProductDecisionBriefV1)

Schema id: `ProductDecisionBriefV1`. This section is the only definition of the first
customer decision question. Nothing in §2–§4, and no service code, may widen it without
amending this section first.

### 1.1 User

An **owner or prospective buyer of a single IRC- or ORC-rated racing yacht** who is
looking at one specific boat (their own, or one they are considering buying or
campaigning) and is deciding whether to spend money on that boat's rating.
Secondary users (designers, rating officials, class associations, fleet analysts) are
out of scope for this brief.

### 1.2 Decision

> "Should I pay for the full Sailing Intelligence Report on this boat — and is there a
> rating-driven reason to act (re-measure, change configuration, or re-assess the
> purchase) — or is the boat's rating position already unremarkable?"

Concretely the service must answer, for one `boat_id`, with evidence:

1. Where does this boat's current rating sit against its design class (over / under
   rated relative to the class model)?
2. Has its rating moved materially across certificates/seasons, and why?
3. Does its on-water performance (RAI) agree or disagree with its rating position?
4. Is there at least one rating lever worth a conversation with the rater (sensitivity)?

The answer is a **verdict + evidence** pair, never a verdict alone. The free surface
shows the verdict headline and the number of supporting findings; the $99 report
shows the findings themselves (ReportFactsV1 sections).

**Candidate default product surface:** the free boat-profile teaser
(`/boats/[boat_id]`) as the question-asker, with the $99 one-off report as the paid
answer. The $290/yr subscription is not the unit of this decision.

### 1.3 Required evidence

Every claim must be traceable to an existing table or ReportFactsV1 fact. No new data
acquisition is required.

| Question | Tables | ReportFactsV1 section / engine |
|---|---|---|
| Identity of boat, design | `boats`, `boat_identities`, `design_classes` | `s02_identity` |
| Current and historical rating | `irc_certificates`, `orc_certificates`, `tcc_snapshots`, `orc_snapshots` | `s03_rating_anatomy`, `s04_rating_evolution` |
| Position against class | `design_classes`, `irc_certificates` / `orc_certificates` | `s05_class_context`, `engines.design_model`, `engines.fleet_wide_model` |
| On-water performance vs rating | `race_results`, `events`, `event_entries`, `boat_events` | `s06_performance`, `engines.rai` |
| Rating levers | certificate dimension fields | `s07_sensitivity`, `s08_optimisation` |
| Formula / rule change exposure | certificate history by rule year | `s09_formula_drift` |
| Comparable rivals | `race_results`, `irc_certificates` | `s10_rivals` |
| Reproducibility | — | `facts_sha256`, `schema_version == "ReportFactsV1"` |

`ReportFactsV1` (`api/src/irc_data/api/services/report/facts_bundle.py`) is the
only versioned answer contract at the time of writing; the decision answer is a
projection of it and must not carry a number that is not in the bundle.

### 1.4 Unsupported cases

Unsupported cases return an explicit `unsupported` status with a machine-readable
reason code. They never return a best-effort verdict. The service must refuse, and the UI
must say so, for:

- Boats with fewer than 3 seasons of race results (reason `insufficient_seasons`).
- Boats with no current valid IRC or ORC certificate (reason `no_current_certificate`).
- Boats whose design class has fewer than 5 boats, so no class regression is
  published (`MIN_BOATS = 5` in `analysis/class_regression.py`; reason `class_too_small`).
  The fleet-wide Tier-C model may be shown only if labelled as such and with lower confidence.
- Boats whose design class has a target with no variance, or whose levers are degenerate
  in the class (reason `degenerate_class_model`).
- Boats with fewer than 5 results remaining after hold-out for the RAI
  (`RAI_MIN_RACES_AFTER_HOLDOUT = 5`; reason `insufficient_races_for_rai`).
- Boats with an unresolved duplicate/identity conflict in `boat_identities`
  (reason `identity_unresolved`).
- Boats whose data sits in `publication_quarantine` or a failed quality verdict
  (reason `data_quarantined`).
- One-off, custom or prototype boats with no design class (reason `no_design_class`).
- Rating systems other than IRC and ORC (e.g. PHRF, ToT, local handicap) and
  non-racing boats (reason `unsupported_rating_system`).
- Questions the data cannot answer: predicting race outcomes, valuing a boat in
  money terms, recommending a purchase, and anything about crew, tactics or sails
  not in the certificate (reason `out_of_scope_question`).

### 1.5 Confidence semantics

Each answer carries a confidence label of `high`, `medium` or `low`, computed
deterministically from data coverage (seasons of results, class size, model tier,
certificate recency) — never from the narrative generator. The label definitions,
thresholds and the calibration against outcomes are defined in §3 (IN-01-03). Until
§3 is approved this brief only fixes the rules below:

- `unsupported` is not a low-confidence answer; it is the absence of one.
- A `low` confidence answer may be shown, but the verdict must be worded as
  "indicative" and must not be used to trigger the paid-report call to action.
- The label must be shown beside the verdict wherever the verdict is shown.

### 1.6 Success metric

Primary: **teaser-to-report conversion on supported boats**, i.e. paid $99 reports ÷
unique supported-boat teaser views, measured over rolling 30 days.

Guardrails:

- Refund/dispute rate on reports for boats shown with `high` or `medium` confidence.
- Share of boat-profile views resolving to `unsupported` (coverage), reported so that
  coverage gaps are visible rather than hidden.
- Outcome events defined in §4 (IN-01-05) are the measurement source; no metric in this
  brief may rely on an event not defined there.

A numeric target is set by Stuart at approval time and recorded in the approval comment.

### 1.7 Approval

Human Gate: Stuart approves §1 by commenting `approved` on IN-01-01. IN-01-02,
IN-01-03 and IN-01-05 do not start until then.

## 2. Query service

Owned by IN-01-02. Implements the evidence side of §1: every fact that leaves the API
through `GET /v1/intelligence/*` carries provenance, or is an explicit incomplete answer.

### 2.1 Contract: IntelligenceAnswerV1

Defined in `api/src/irc_data/api/schemas/intelligence.py` (IN-01-03 and IN-01-05 append to
that file after IN-01-02).

| Field | Type | Meaning |
|---|---|---|
| `query`, `version` | str | Registry name and version, e.g. `boat_profile` / `v1`. |
| `data` | object | The wrapped service response, unchanged. `{}` when nothing could be read. |
| `provenance` | `Provenance` | Always present, also on incomplete answers (§2.3). |
| `incomplete` | bool | `true` iff `missing` is non-empty. |
| `missing` | list[str] | Machine-readable reason codes (§2.4); `[]` when complete. |
| `evidence` | list[`EvidenceRef`] | `{source, ref, label, value}` pointers to the rows/sections used. |
| `confidence` | object \| null | Placeholder. `ConfidenceAssessmentV1` is defined and filled by §3 (IN-01-03). |

`Provenance` = `{dataset_version, facts_sha, lineage: list[str], freshness_at, quality_status}`.
`EvidenceRef` is reused from `schemas/analyst.py` (AI-01-03) once that file exists; until then
`schemas/intelligence.py` defines a local stand-in with the same name.

### 2.2 Query registry

Named, versioned queries in `services/intelligence/queries.py`. Each wraps an existing service
call and does not change its response. A query is addressed as `<name>.<version>`.

| Query | Route | Wraps | Required input |
|---|---|---|---|
| `boat_profile.v1` | `GET /v1/intelligence/boats/{id}` | `routers/boats.get_boat` | `boat_id` |
| `rating_book_search.v1` | `GET /v1/intelligence/search?q=` | `routers/search.fuzzy_search` | `q` |
| `fleet_explorer.v1` | `GET /v1/intelligence/fleet/{design}` | `routers/designs.get_design_fleet` | `design` |
| `report_facts.v1` | `GET /v1/intelligence/report-facts/{boat_id}` | `services/report/facts_bundle.build_report_facts` | `boat_id` |

Also: `GET /v1/intelligence/queries` lists the registry and
`GET /v1/intelligence/query/{name}.{version}?...` runs any registered query; an unregistered
name or version is the only 404 the service returns. A new version of a query is a new
registry entry; an existing version's output shape never changes.

### 2.3 Provenance rules

- `dataset_version`: `irc-canonical-YYYYMMDD` from the newest `boats.updated_at`. Identical for
  every answer served from the same dataset state; `irc-canonical-empty` on an empty database.
- `facts_sha`: sha256 of the canonical JSON (sorted keys, compact separators) of `data`. For
  `report_facts.v1` it is the bundle's own `facts_sha256` (ReportFactsV1), so the two never diverge.
- `lineage`: stable refs to what was read, e.g. `boats:id=17213`,
  `tcc_snapshots:boat_id=17213,snapshot_date=2026-02-01`, `report_facts:section=s03_rating_anatomy`.
  Always at least one ref, also when incomplete. `evidence.lineage_refs_from_report()` flattens a
  `transform.lineage.LineageReport` (assertion -> extraction batch -> artifact) into the same
  ref form for callers holding canonical assertions.
- `freshness_at`: newest of the rows' `updated_at` and the rating snapshot dates used; `null`
  only when no row was read.
- `quality_status`: `incomplete` if `missing` is non-empty; else `degraded` if any
  `publication_quarantine` row is `active` or `freshness_at` is more than 365 days old;
  else `ok`. A database without the quarantine table is treated as no quarantine.

### 2.4 Incomplete-data rules

An incomplete answer is HTTP 200 with `incomplete=true`; it is never a best-effort verdict
(§1.4). Partial `data` is still returned where it exists. Codes reuse §1.4 reasons where one exists.

| Query | `missing` code | Condition |
|---|---|---|
| all | `boat_id` / `q` / `design` | required input absent or blank |
| `boat_profile.v1`, `report_facts.v1` | `boat_not_found` | no such boat |
| `boat_profile.v1` | `no_current_certificate` | no IRC snapshot and no ORC certificate (§1.4) |
| `boat_profile.v1`, `report_facts.v1` | `no_design_class` | boat has no design (§1.4) |
| `fleet_explorer.v1` | `design_not_found` | no boats in that design |
| `fleet_explorer.v1` | `class_too_small` | fewer than `MIN_BOATS` (5) boats (§1.4) |
| `fleet_explorer.v1` | `sort` | sort not one of `tcc`, `name`, `country` |
| `report_facts.v1` | `section:<id>` / `engine:<id>` | that builder/engine returned `_error` |
| `report_facts.v1` | `insufficient_races_for_rai` | RAI engine returned no result (§1.4) |

A search with zero hits is a complete answer (`total: 0`), not an incomplete one.

Not yet enforced in v1 (the codes stay reserved for a later version): `insufficient_seasons`,
`degenerate_class_model`, `identity_unresolved`, `data_quarantined` (per boat),
`unsupported_rating_system`, `out_of_scope_question`.

### 2.5 Latency budget

p95 <= 300 ms per request, measured in `tests/api/test_intelligence_answers.py` against a warm
in-memory SQLite fixture (40 rounds x 4 routes). The wrapped PostgreSQL-only service calls are
replaced by SQLite-backed stand-ins in that test, so it bounds the service's own overhead
(provenance, hashing, serialisation), not the wrapped SQL. `report_facts.v1` is dominated by
`build_report_facts` (all eleven section builders plus the engines) and is far slower on the full
production database; this is the cost of the wrapped call, and a cached or pre-computed bundle
is a follow-up, not part of this card.

### 2.6 Consumers

The Analyst (AI-01-05) switches to these queries as its tool surface later. Tools must pass
`provenance` and `missing` through unmodified and must not state a number that is absent from `data`.

## 3. Confidence

Owned by IN-01-03. Replaces the placeholder in §1.5 and §2.1. Status: **DRAFT — the weights
(§3.1) and the golden-boat bands (§3.4) are proposals awaiting the Human Gate: Stuart approves
them by commenting `approved` on IN-01-03.** Changing any number here is a new confidence version.

Schema id: `ConfidenceAssessmentV1` in `api/src/irc_data/api/schemas/intelligence.py`:
`{score: 0..1, band: high|medium|low, factors: [{name, weight, value, note}], limitations: [str]}`.
Implemented by `services/intelligence/confidence.py::assess(inputs)`, a pure function: same
inputs, same assessment. `answers.py` attaches it to **every** `IntelligenceAnswerV1.confidence`,
including incomplete answers. Confidence is computed from data only, never from the narrative
generator, and a factor that cannot be read scores 0 (never guessed).

### 3.1 Factors and weights

`score = Σ weight × value`, rounded to 6 decimals. Weights sum to 1.00.

| Factor | Weight | Value (0..1) | Inputs |
|---|---|---|---|
| `coverage` | 0.25 | `min(seasons_with_results / 3, 1)` | distinct calendar years in `race_results` for the boat |
| `source_agreement` | 0.20 | fraction of the boat's TCC readings (latest `cert_year` in `tcc_snapshots`) within 0.001 of the latest reading; one reading = 0.5 (cannot be cross-checked); none = 0 | `tcc_snapshots` |
| `identity_certainty` | 0.20 | `1 − pending_dupe_cluster_membership` (1 if the boat has no `PENDING` row in `dupe_review_queue`, else 0) | `dupe_review_queue` |
| `recency` | 0.20 | `max(0, 1 − days_since_latest_fact / 365)` | newest of snapshot date, race date, answer `freshness_at` |
| `model_evidence` | 0.15 | `0.5 × N-score + 0.5 × R²-score`; no model = 0 | `engines.design_model` (else `fleet_wide_model`) |

`model_evidence` thresholds come from `docs/model/METHODOLOGY.md` (class regression):
N-score is 0 below 5 boats (`MIN_BOATS`), 0.5 for 5–14 (below `MIN_BOATS_FULL_CV`, "limited
data"), 1 for 15 or more; R²-score is 1 when R² ≥ 0.3 (the Tier A floor), else 0. Cross-validated
R² is used when the model reports one, because in-sample R² flatters small classes.

Answers that are not about one boat (`rating_book_search`, `fleet_explorer`) have no seasons,
TCC readings, identity status or model, so those factors score 0 and the limitations say so.
Their confidence is therefore low by construction; that is intended, they are not verdicts.

### 3.2 Bands

`high` ≥ 0.75, `medium` ≥ 0.45, otherwise `low`. The boundaries are inclusive (0.45 is
`medium`, 0.75 is `high`). Wording rules for each band are those of §1.5.

### 3.3 Limitations

Every factor with `value < 0.5` adds one entry to `limitations`, naming the factor, its value
and the reason (e.g. `Stale data (recency=0.00): latest fact 700 day(s) old against 365`). A
factor at exactly 0.5 adds none. `limitations == []` means no factor is below 0.5.

### 3.4 Golden boats (holdout)

`tests/api/test_confidence_assessment.py` pins three synthetic boats. Expected bands are the
Human Gate; the scores are what the §3.1 formula gives.

| Boat | Inputs | Score | Band (Stuart to confirm) |
|---|---|---|---|
| A: well-documented J/109 | 4 seasons; 3 TCC readings within 0.001; no pending cluster; 30 days; class model N=40, R²=0.72 | 0.9836 | **high** |
| B: thin one-off | 2 seasons; 2 agreeing TCC readings; no pending cluster; 400 days; no model | 0.5667 | **medium** |
| C: stale, pending duplicate | 1 season; 1 TCC reading; pending cluster; 700 days; model N=4, R²=0.2 | 0.1833 | **low** |

### 3.5 Consumers

`web/src/lib/intelligence.ts` mirrors the type; `web/src/components/intelligence/ConfidenceBadge.tsx`
renders score, band and limitations. It is mounted on a page by IN-01-04.

## 4. Outcome events

Owned by IN-01-02 / IN-01-03 / IN-01-05 (this section: IN-01-05).
