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

Owned by IN-01-02 / IN-01-03 / IN-01-05 (this section: IN-01-02).

## 3. Confidence

Owned by IN-01-02 / IN-01-03 / IN-01-05 (this section: IN-01-03).

## 4. Outcome events

Owned by IN-01-02 / IN-01-03 / IN-01-05 (this section: IN-01-05).
