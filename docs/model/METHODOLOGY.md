# SailRatings model methodology

| Field | Value |
|---|---|
| Schema | `ModelMethodologyV1` |
| Version | `1.0.0` |
| Date | 2026-10-10 |
| Card | SM-01-01 (Epic SM-01) — SPEC-SM, SPEC-017, SPEC-SM-01-08 |
| Status | **DRAFT — awaiting Stuart's approval** (Human Gate; "Human Gate off" counts as approved) |
| Code of record | `api/src/irc_data/analysis/` at the SM-01-02..08 merge (`regression.py`, `class_regression.py`, `performance.py`, `rai.py`, `rule_drift.py`, `temporal.py`, `what_if.py`, `optimizer.py`, `comparative.py`, `race_prep.py`, `backtest.py`) |
| Golden evidence | `api/tests/report/golden/{chilli_pepper,diablo_j,kestrel}/golden_report_facts_v1.json` |
| Companion | [GLOSSARY.md](GLOSSARY.md); [docs/architecture/rai.md](../architecture/rai.md) (RAI implementation notes — cited, not merged); [docs/architecture/scoring.md](../architecture/scoring.md) (identity match scoring — unrelated to model numbers) |

This document gives every number the product publishes a written definition, the
threshold below which it is withheld, a versioning rule and a worked example. It
describes the code as it is. Where two code paths define the same number
differently, both are written down in [Open decisions for Stuart](#open-decisions-for-stuart)
rather than silently picking one.

**The IRC rating formula is confidential.** No section below claims to know it.
Every coefficient is a statement about what the fleet's published ratings are
*consistent with*, never about what the rule *does*.

## How to read this document

* **Worked examples are checked.** Every figure in a `worked-example` block is
  quoted from a golden bundle and re-verified against it (within 1e-3) by
  `api/tests/report/test_methodology_doc.py`. If a golden bundle is
  re-snapshotted and a figure moves, that test fails until this document is
  updated. Arithmetic on those figures is written as `derived: <expression> = <result>`
  and is re-evaluated by the same test. Named code constants (for example
  `MIN_BOATS_FULL_CV`) are written by name inside worked examples; their values are
  given in the Thresholds subsection of the same component.
* **Golden boats.** KESTREL (Sunfast 3300, GBR6779R, 18 certificate-backed boats in
  class), CHILLI PEPPER (Sunfast 3300, GBR1663R, 22) and DIABLO-J (J/92, GBR9205R,
  4 boats — the thin-class case). See `api/tests/report/golden/README.md`.
* **"Withheld"** means the number is not computed or not published; the output
  carries an explicit status instead of a confident-looking value.
* **Two code paths.** The design report (`ReportFactsV1`, hence all three golden
  bundles) is built from the original engines `regression.py` and `performance.py`.
  The SM-01-02 / SM-01-03 engines `class_regression.py` and `rai.py` are served by the
  analytics API. Each component below documents both where they differ.

## Component index and versioning

| # | Component | Contract / version id | Section |
|---|---|---|---|
| 1 | Class regression | `ClassRegressionV1` (analytics API); report path: `RegressionResult` inside `ReportFactsV1` | [Class regression](#class-regression) |
| 2 | Racing Advantage Index | `RAIComputationV1` / `rai-config-v1` (API); report path: `RAIResult` | [Racing Advantage Index](#racing-advantage-index-rai) |
| 3 | Smart-boat cohort | no own schema id; part of the two regression contracts | [Smart-boat cohort](#smart-boat-cohort) |
| 4 | Rule drift | `RuleDriftV1` (API); report path: `FormulaDriftFacts` | [Rule drift](#rule-drift) |
| 5 | Δ TCC → seconds per hour | convention, constant `SECONDS_PER_HOUR_PER_TCC_POINT` | [Δ TCC to seconds per hour](#δ-tcc-to-seconds-per-hour) |
| 6 | One-design / "not meaningful" | rules inside the contracts above | [One-design and "not meaningful"](#one-design-and-not-meaningful) |
| 7 | What-if estimator | `WhatIfEstimateV1`, `RecommendationV1` | [What-if estimator](#what-if-estimator) |
| 8 | Comparative metrics and race-prep conditions | `HeadToHeadV1`, `DesignComparatorV1`, `FleetSummaryV1`, `RacePrepFactsV1` | [Comparative metrics and race preparation](#comparative-metrics-and-race-preparation) |
| 9 | Backtesting and golden tolerances | SM-01-08 gate | [Backtesting and golden tolerances](#backtesting-and-golden-tolerances) |

**Versioning rule (applies to every component).** A *definition, formula, input
filter, threshold or constant* change is a **methodology change**. It requires
(a) a new version id on the affected contract (`…V2`, `rai-config-v2`, …) or a
new minor version of this document if the contract has no id of its own,
(b) a re-snapshot of the golden bundles with every moved figure reviewed
(`git diff api/tests/report/golden`, per the golden README), and (c) Stuart's
approval of the diff of this document. A pure refactor that leaves the golden
bundles bit-identical is not a methodology change. Prior numbers must stay
reproducible: each contract that carries fingerprints (`dataset_fingerprint`,
`config_fingerprint`, `dataset_version`) keeps them so a number can be tied to
the data and ruleset that produced it.

## Class regression

### Definition

For one design class, how each certificate/snapshot *lever* (length, draft,
declared headsails, …) relates to TCC across the boats of the class. It answers
"which measurements are the published TCCs of this class most consistent with?" —
not "what does the rule charge for X". Output per lever: a standardised
coefficient (comparable across levers), a coefficient per display unit, a rank, and
the model's R², N, collinearity warnings and an interpretation sentence.

### Formula

*Report path (`regression.py`, used by the golden bundles).* Ridge regression,
`TCC ≈ α + Σ βⱼ·zⱼ`, fitted on levers standardised to mean 0 / SD 1
(`StandardScaler`), target TCC left in raw units. Ridge penalty chosen by `RidgeCV`
over 20 log-spaced values from 0.01 to 100 (`RIDGE_ALPHAS`), scored by CV R².

* `std_beta` = the fitted coefficient on the standardised lever = **ΔTCC per +1
  standard deviation of that lever**. It is therefore in TCC units and small
  (≈0.001–0.08 in the goldens).
* `beta_per_unit` = `std_beta / SD(lever) × unit_scale`, the ΔTCC per display unit
  (`per 0.1m`, `per sail`, `per 100kg`, `per person`; scale factors in
  `SCALE_FACTORS`).
* Coefficients are ranked by `|std_beta|`; rank 1 is the "strongest lever".
* `r_squared` = in-sample R² of the fitted model; `r_squared_cv` = best
  cross-validated R² from `RidgeCV`; `alpha` = chosen ridge penalty.
* Collinearity warning for any lever pair with `|r| > 0.9`.

*API path (`class_regression.py`, `ClassRegressionV1`).* Ordinary least squares with
**both** levers and target standardised (sample SD, ddof = 1), so `std_beta` is the
conventional standardised regression coefficient (SD of TCC per SD of lever), and
`beta_per_unit = std_beta · SD(TCC) / SD(lever) · display_scale`. Adds adjusted R².
IRC and ORC are fitted separately and never pooled; ORC targets are `gph` and the
triple numbers (`triple_low`, `triple_med`, `triple_high`).

### Inputs

* Report path: latest TCC snapshot per boat in the design class
  (`COALESCE(design_canonical, design)`), plus the latest `irc_certificates` row for
  Tier A.
  * **Tier A** levers (15): `displacement`, `draft`, `lh`, `p`, `e`, `j`, `hlu`,
    `hlp`, `muw`, `mhw`, `stl`, `sym_slu`, `sym_sf`, `headsails`, `spinnakers`.
  * **Tier B** levers (7, snapshot fields only): `lh`, `beam`, `draft`,
    `headsails`, `spinnakers`, `crew`, `dlr`.
  * **Tier C** = the Tier B levers fitted across **all** designs ("All Designs
    (Fleet-Wide)").
  * A lever is used only if at least 50 % of the class has a value; a row is used
    only if every used lever and TCC are present. (The golden Tier A models carry 13
    coefficients: `sym_slu` and `sym_sf` are absent, consistent with that coverage
    rule.)
* API path: IRC levers `hsa`, `spa`, `crew`, `draft`, `displacement`, `lh`; ORC levers
  `sail_area_upwind`, `sail_area_downwind`, `displacement`, `draft`,
  `stability_index`, `dynamic_allowance`. Declared sail counts are deliberately
  excluded there (parsed only on newer certificates; would drop ~30 % of a class).

### Thresholds (below which the output is withheld)

| Rule | Value | Constant | Effect |
|---|---|---|---|
| Minimum class size for a regression | 5 boats | `MIN_BOATS_FOR_REGRESSION`, `MIN_BOATS` | Report path: fewer than 5 boats → `correlation_only` (pairwise correlations, no coefficients, no R²). API path: `WithheldClassResult` with reason `below threshold: N=<n> < MIN_BOATS=5`. |
| Fewer than 2 boats | 2 | — | Report path returns nothing. |
| Minimum boats with certificates for Tier A | 5 | `MIN_BOATS_TIER_A` | Else Tier B. |
| Tier A is used only if in-sample R² exceeds | 0.3 | (inline in `analyze_design_sensitivity`) | Else the Tier B model is reported instead. |
| Cross-validation scheme | N < 15: leave-one-out; N ≥ 15: 5-fold | `MIN_BOATS_FULL_CV` | N < 15 also appends "[Limited data — interpret with caution.]" to the interpretation. |
| Constant lever | variance ≤ 1e-12 | `MIN_FEATURE_VARIANCE` | Lever dropped (API path); target with no variance → class withheld ("no lever variance within class" / "target … is constant within class"). |
| Collinearity warning | `|r| > 0.9` | `_check_collinearity` default | Warning text attached; coefficients still shown. |
| ORC "tight fit" flag | R² ≥ 0.8 | `ORC_TIGHT_R2` | Flag only (ORC is VPP-derived, so tighter fits are expected); no withholding. |
| One-design / low headroom | TCC spread < 0.0010 | inline in `analyze_design_sensitivity` | See [One-design and "not meaningful"](#one-design-and-not-meaningful). |

There is **no R² floor below which a fitted class is withheld**. In-sample R² can
be very high on a small class while cross-validated R² is poor (see the worked
example). Readers and prose generators must therefore quote `r_squared_cv`
alongside `r_squared`; recorded under [Open decisions for Stuart](#open-decisions-for-stuart).

### Versioning rule

Contract ids: `ClassRegressionV1`, `BoatClassPositionV1` (API path). The API path
stamps every run with a `dataset_version` (explicit promoted batch id, else a
SHA-256 content fingerprint of the input rows) so a re-run on unchanged data
reproduces the same version and R². Lever sets, tier feature lists, ridge grid and
the thresholds above are part of the methodology: changing any of them is a
methodology change (see the versioning rule above).

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL — Sunfast 3300, Tier A.**

* Class model: `engines.design_model.n_boats = 18` boats,
  `engines.design_model.r_squared = 0.9942` in-sample and
  `engines.design_model.r_squared_cv = 0.5618` cross-validated; chosen ridge penalty
  `engines.design_model.alpha = 0.0695`.
* N is above `MIN_BOATS_FOR_REGRESSION` and above `MIN_BOATS_FULL_CV`, so 5-fold CV
  was used and no "Limited data" note was added. In-sample R² is above the Tier A
  floor, so Tier A is the reported model.
* Strongest lever: `engines.design_model.coefficients[field=mhw].std_beta = 0.007`
  (rank 1) and `engines.design_model.coefficients[field=mhw].beta_per_unit = 0.009303`
  per 0.1 m of `mhw`.
* Standardisation check: one SD of the lever is
  `derived: 0.007 / (0.009303 / 0.1) = 0.0752` m of `mhw`, i.e. a lever 1 SD larger
  goes with 0.007 higher TCC.
* Collinearity: `p ↔ j` r = −0.98, `p ↔ muw` r = −0.94 and `j ↔ muw` r = 0.95 are all
  past the 0.9 threshold, so `engines.design_model.collinearity_warnings` says
  individual coefficients should be read with caution.
* Fleet-wide Tier C model: `engines.fleet_wide_model.n_boats = 298`,
  `engines.fleet_wide_model.r_squared = 0.9438`,
  `engines.fleet_wide_model.r_squared_cv = 0.9312`; top lever
  `engines.fleet_wide_model.coefficients[field=draft].std_beta = 0.0783`.
<!-- /worked-example -->

<!-- worked-example boat=chilli_pepper -->
**CHILLI PEPPER — Sunfast 3300, Tier A: in-sample R² flatters a small class.**

* `engines.design_model.n_boats = 22`, `engines.design_model.r_squared = 0.9778` but
  `engines.design_model.r_squared_cv = 0.1661`. The in-sample / cross-validated gap is
  `derived: 0.9778 - 0.1661 = 0.8117`. The model reproduces the 22 certificates it
  was fitted on but predicts held-out boats poorly, so the Tier A coefficients rank
  levers for *this fitted class* and must not be presented as stable effects. The
  largest standardised effect is only
  `engines.design_model.coefficients[field=mhw].std_beta = 0.0052` TCC per SD.
* Fleet-wide Tier C: `engines.fleet_wide_model.n_boats = 257`,
  `engines.fleet_wide_model.r_squared = 0.9505`,
  `engines.fleet_wide_model.r_squared_cv = 0.9176`.
<!-- /worked-example -->

<!-- worked-example boat=diablo_j -->
**DIABLO-J — J/92: below the minimum class size, regression withheld.**

* `engines.design_model.n_boats = 4` is below `MIN_BOATS_FOR_REGRESSION`, so the
  report carries model tier `correlation_only`: pairwise correlations with TCC only
  (for example `engines.design_model.correlations.draft = 0.9466`), no coefficients, no
  R². With four boats a correlation of `engines.design_model.correlations.headsails = -1.0`
  is an artefact of the sample, not a finding.
* Downstream sections degrade accordingly:
  `sections.s03_rating_anatomy.explained_variance_pct = 0` and an empty
  decomposition; `sections.s07_sensitivity.r_squared = 0.0` and no coefficients.
* The fleet-wide Tier C model is still published because its own N is large:
  `engines.fleet_wide_model.n_boats = 819`,
  `engines.fleet_wide_model.r_squared = 0.9281`,
  `engines.fleet_wide_model.r_squared_cv = 0.9221`.
<!-- /worked-example -->

## Racing Advantage Index (RAI)

### Definition

"Is this boat finishing better or worse than her rating predicts?" One observation
per race; the index is their mean, in percentile points (×100). **Positive = beating
the rating.** Published with a 95 % confidence interval and the race count so a
short hot streak is not read as proven performance.

### Formula

Per race `r` for boat `b`:

```
actual_pct_r    = place_r / fleet_size_r                 (official corrected-time placing)
expected_pct_r  = (rank of b's TCC among the race's field) / field_size
advantage_r     = (expected_pct_r − actual_pct_r) × 100
RAI_b           = mean(advantage_r)
```

*Report path (`performance.compute_rai`, golden bundles).* The field is every
finished result with a rating in the same event / race / date. Higher TCC is treated
as faster, so `rank = 1 + #(field ratings strictly above the boat's)`. If the rating
or field is missing the expectation falls back to 0.5. **CI:** two-sided Student-t
interval on the mean, `mean ± t(0.975, n−1) · SEM`, when `n ≥ 3`; for `n < 3` the
interval collapses to the point estimate. Interpretation bands on the point estimate:
`> +5` "Strong positive", `0..+5` "Slightly positive", `−5..0` "Slightly negative",
`< −5` "Negative".

*API path (`rai.py`, `RAIComputationV1`, `rai-config-v1`).* Same advantage formula,
but `rank = 1 + #(distinct field ratings strictly below the boat's)` (ties share the
best rank), the field is distinct ratings per resolved identity, and the CI is a
**bootstrap-t** with 2000 resamples and a pinned seed (`BOOTSTRAP_SEED = 0x5A11`),
falling back to a percentile bootstrap when the standard error is ≈ 0 and to a
degenerate interval for `n < 2`. Interpretation is by the CI: wholly above 0 =
out-performing, wholly below 0 = under-performing, spanning 0 = "racing to her
rating within noise". **The two paths rank the field in opposite directions** — see
[Open decisions for Stuart](#open-decisions-for-stuart).

**TWS-band splits (API path).** Where a race's source payload carries a true-wind
speed reading (`tws`, `tws_kt`, `true_wind_speed`, `wind_speed`, …; values ≤ 0 or
> 60 kn are sensor noise), RAI is also computed per band, half-open `[lo, hi)`:
`light 0–8 kn`, `medium 8–14 kn`, `fresh 14–20 kn`, `heavy 20+ kn`. Races without
wind data enter no band and are never pooled into one.

### Inputs

Finished, placed results with `fleet_size > 1`, IRC-rated, not twilight
(`BASIC_IRC_FILTER` in `irc_data/analysis/filters.py`), per resolved `boat_id`
(identity resolution from DP-04-04). Single-boat fields carry no information and
are kept on the record but not scored.

### Thresholds (below which the output is withheld)

| Rule | Value | Constant | Effect |
|---|---|---|---|
| Minimum scored races per boat | 5 | `DEFAULT_MIN_RACES` | API path: `status = "insufficient_data"`, `rai = None`, boat excluded from class baselines. |
| Minimum races per TWS band | 3 | `DEFAULT_MIN_BAND_RACES` | Band reports `insufficient_data`, `rai = None`. |
| Races for a non-degenerate CI | 3 (report path) / 2 (API path) | inline | Below, CI = point estimate. |
| Confidence level | 0.95 | `DEFAULT_CONFIDENCE_LEVEL` | — |
| Bootstrap resamples | 2000 | `DEFAULT_BOOTSTRAP_RESAMPLES` | API path only. |

The report path has **no minimum-race withholding** (any boat with at least one
usable race gets a number); see [Open decisions for Stuart](#open-decisions-for-stuart).

### Versioning rule

API path: `RAI_SCHEMA_VERSION = "RAIComputationV1"`, config `rai-config-v1`; every
result carries `dataset_fingerprint` (SHA-256/16 hex over sorted observation keys)
and `config_fingerprint` (over the whole ruleset incl. thresholds, resamples, seed,
band edges). Any rule change ships `rai-config-v2` so prior numbers stay
reproducible. Report path: governed by the golden-bundle gate (SM-01-08). Further
implementation detail: [docs/architecture/rai.md](../architecture/rai.md).

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL — strongly out-performing her rating.**

* `engines.rai.n_races = 39` scored races (`engines.rai.wins = 1` win,
  `engines.rai.podiums = 4` podiums).
* Mean expected percentile `engines.rai.avg_expected_pct = 0.643`, mean actual
  percentile `engines.rai.avg_finish_pct = 0.282`: she finishes about 36 percentile
  points better than her TCC predicts, which is the published
  `engines.rai.rai = 36.13` (the two rounded percentiles give 36.1; the difference
  is rounding of the inputs).
* 95 % CI `engines.rai.ci_lower = 26.66` to `engines.rai.ci_upper = 45.6`. The Student-t
  interval is symmetric, so `derived: (26.66 + 45.6) / 2 = 36.13` recovers the mean
  and `derived: (45.6 - 26.66) / 2 = 9.47` is the half-width. The CI is wholly above
  0, so the interpretation is the "Strong positive" band.
<!-- /worked-example -->

<!-- worked-example boat=chilli_pepper -->
**CHILLI PEPPER — positive, but with a wide interval.**

* `engines.rai.rai = 12.33` from `engines.rai.n_races = 44` races (`engines.rai.wins = 3`
  wins, `engines.rai.podiums = 4` podiums), 95 % CI `engines.rai.ci_lower = 4.14` to
  `engines.rai.ci_upper = 20.51`.
* Expected percentile `engines.rai.avg_expected_pct = 0.432` versus actual
  `engines.rai.avg_finish_pct = 0.309`. The CI excludes 0, so the advantage is
  distinguishable from noise, but the interval is `derived: 20.51 - 4.14 = 16.37`
  points wide, so the point estimate must not be quoted without it.
<!-- /worked-example -->

<!-- worked-example boat=diablo_j -->
**DIABLO-J — long history, tight interval.**

* `engines.rai.rai = 26.58` from `engines.rai.n_races = 199` races
  (`engines.rai.wins = 4`, `engines.rai.podiums = 25`), 95 % CI
  `engines.rai.ci_lower = 22.99` to `engines.rai.ci_upper = 30.17`.
  `derived: (22.99 + 30.17) / 2 = 26.58` and the half-width is
  `derived: (30.17 - 22.99) / 2 = 3.59`, against 9.47 for KESTREL's 39 races: more
  races, tighter interval.
* Expected `engines.rai.avg_expected_pct = 0.625` versus actual
  `engines.rai.avg_finish_pct = 0.359`.
<!-- /worked-example -->

*Not in a golden bundle (so not machine-checked here):* the minimum-race and TWS-band
rules are pinned by the `rai.py` golden fixture in
[docs/architecture/rai.md §5](../architecture/rai.md) — four scored races →
`insufficient_data` / `rai = None`, five → `ok`; wind readings 4 × 6 kn (`light`)
and 4 × 16 kn (`fresh`) populate exactly those two bands; merging the rival's
results into the focal boat shifts RAI by the pinned −25.0.

## Smart-boat cohort

### Definition

The reference group of "boats that do well in this class", used to show where the
top performers' measurements differ from the class mean (the "where the points
hide" teaser). Defined differently on the two paths.

### Formula

* *Report path (`performance.get_smart_boats`).* Rank the class's boats with at
  least `min_races = 3` finished races by average finish percentile (lowest first);
  the cohort is the top `top_pct = 0.1` of them: `n_top = max(1, int(n_with_races × 0.1))`.
  A cohort member's RAI is `(0.5 − avg_finish_pct) × 100` (no expected-percentile
  adjustment). Reported: cohort members, their mean levers (`lh`, `beam`, `draft`,
  `headsails`, `spinnakers`, `crew`, `dlr`) against the class mean, and the fleet RAI
  distribution (mean / median / std / min / max).
* *API path (`class_regression._smart_cohort`).* The cohort is the
  **lowest-target quintile** of the regressed class (`n = max(1, ceil(0.2 · N))`,
  lowest TCC first); cohort means are reported per lever and per boat position.
  This is a rating-based cohort, not a results-based one.

### Inputs

Report path: latest TCC snapshot per boat, `mv_boat_performance_summary`
(finished races, average finish percentile). API path: the same rows as the class
regression.

### Thresholds (below which the output is withheld)

* Report path: boats with fewer than 3 finished races are not eligible; with no
  eligible boats the cohort is empty (`n_top = 0`).
* API path: inherits the class regression thresholds — a withheld class has no cohort.
* If the performance view `mv_boat_performance_summary` does not exist, the report
  path falls back to a variant that **returns an empty cohort** and an empty fleet
  distribution (it reports only `n_total` and `n_with_races`).

### Versioning rule

No schema id of its own: the cohort rule is versioned with the regression contract
that carries it. Changing `top_pct`, `min_races`, the quintile rule or the ranking
key is a methodology change.

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL — Sunfast 3300.**

* `engines.smart_boats.n_total = 35` boats in class, of which
  `engines.smart_boats.n_with_races = 35` have race results.
* Report-path rule: `derived: 35 * 0.1 = 3.5`, truncated by `int()` to a cohort of 3
  boats. API-path rule on the Tier A class model of `engines.design_model.n_boats = 18`
  boats: `derived: 18 * 0.2 = 3.6`, rounded up by `ceil` to a cohort of 4.
* The golden bundle shows an empty `smart_boats` list and an empty
  `fleet_rai_distribution`. That is the signature of the fallback path (no `n_smart`,
  `class_means` or `smart_boat_means` keys in the bundle), consistent with the
  fixture databases carrying no `mv_boat_performance_summary`. The empty cohort is
  the recorded golden behaviour; it is **not** evidence that no boat in the class is
  "smart".
<!-- /worked-example -->

<!-- worked-example boat=chilli_pepper -->
**CHILLI PEPPER — Sunfast 3300.** `engines.smart_boats.n_total = 36` and
`engines.smart_boats.n_with_races = 36`; `derived: 36 * 0.1 = 3.6` truncates to a
cohort of 3 on the report-path rule, and the golden bundle again records the empty
fallback cohort.
<!-- /worked-example -->

<!-- worked-example boat=diablo_j -->
**DIABLO-J — J/92.** `engines.smart_boats.n_total = 4`,
`engines.smart_boats.n_with_races = 4`: `derived: 4 * 0.1 = 0.4` truncates to 0, so the
`max(1, …)` floor gives a cohort of exactly one boat — a "top 10 %" that is a quarter
of the class. The golden bundle records the empty fallback cohort.
<!-- /worked-example -->

## Rule drift

### Definition

Telling owners when their rating changed because the *rule* changed, not the boat.
IRC re-issues certificates each cycle. A boat whose measurements are unchanged
between two consecutive snapshots but whose TCC moved gives a clean read of rule
movement. The set of such boats is the **stable-certificate cohort**.

### Formula

* **Stable cohort.** A consecutive snapshot pair is *stable* when `lh`, `beam`,
  `draft`, `headsails` and `spinnakers` are all equal in both snapshots
  (a missing value does not break stability). `tcc_delta = tcc_to − tcc_from`.
  Pairs are grouped into rating cycles by `(date_from, date_to)`.
* **Cohort statistics** per cycle and per class (`CohortDriftStats`): `n_stable`,
  `n_total`, mean / median / SD (ddof = 1) of `tcc_delta`, `pct_decreased`,
  Cohen's d = mean / SD.
* **Significance test.** One-sample t-test of `tcc_delta` against 0
  (`p_value_ttest`) and Wilcoxon signed-rank (`p_value_wilcoxon`). Prose labels:
  `p < 0.001` "highly significant", `p < 0.05` "statistically significant",
  otherwise "not statistically significant".
* **Lever attribution.** Fit the Tier B ridge (see [Class regression](#class-regression))
  on the fleet at each end of the cycle and compare standardised coefficients:
  change `> +0.001` "taxed more", `< −0.001` "eased", else "stable"
  (`LEVER_EPSILON`).
* **Per-boat decomposition** (`BoatDriftDecompositionV1`):
  `rule_movement = stable-cohort mean drift`;
  `boat_movement = total TCC change − rule_movement`.
* A cycle whose `|mean drift| < 0.0005` (`DRIFT_EPSILON`) is described as "no
  measurable rule drift".

*Report path (`temporal.analyze_fleet_drift` via `facts_builders.build_formula_drift`,
the golden `s09_formula_drift` section):* the same stable-boat idea over a single
window; `drift_observed = |fleet mean drift| > 0.001`; a measurement is listed as
affected when `|coefficient change| > 0.2`; `window_years` is the window length in
whole years (minimum 1).

### Inputs

`tcc_snapshots` joined to `boats` (class = `COALESCE(design_canonical, design)`).
Measurements compared: `lh`, `beam`, `draft`, `headsails`, `spinnakers`
(`MEASUREMENT_LEVERS`). Lever attribution uses all boats with a TCC snapshot on each
cycle date.

### Thresholds (below which the output is withheld)

| Rule | Value | Constant | Effect |
|---|---|---|---|
| Stable boats for a per-class statistic | 3 | `MIN_CLASS_COHORT` | Class rollup omitted below this. |
| t-test | `n ≥ 3` and SD > 0 | inline | Else `p_value_ttest = None`. |
| Wilcoxon | `n ≥ 6` and some non-zero delta | inline | Else `p_value_wilcoxon = None`. |
| Boats per side for lever attribution | 10 | `MIN_BOATS_LEVER_REGRESSION` | Else no attribution (empty list). |
| "Lever has moved" | `|Δ std coefficient| > 0.001` | `LEVER_EPSILON` | Else "stable". |
| "No measurable drift" | `|Δ TCC| < 0.0005` | `DRIFT_EPSILON` | Narrative only. |
| Report path: drift observed | `|mean drift| > 0.001` | inline | Else `drift_observed = false`. |

### Versioning rule

API path contract `RuleDriftV1` (`RULE_DRIFT_VERSION`). Changing the stability
definition, the tests, the epsilons or the attribution method is a methodology change.
All findings are framed as "consistent with", never as the formula.

### Worked example

<!-- worked-example boat=diablo_j -->
**DIABLO-J — a 17-year window with no drift observed, but a −0.015 TCC move.**

* `sections.s09_formula_drift.window_years = 17`; the bundle records
  `drift_observed` false and no affected measurements for the J/92 class, so the class
  mean drift in the window did not exceed 0.001 in magnitude.
* Her TCC went from `sections.s04_rating_evolution.first_snapshot_tcc = 1.011` (2009
  snapshot) to `sections.s04_rating_evolution.latest_snapshot_tcc = 0.996`, a total move of
  `derived: 0.996 - 1.011 = -0.015`, matching
  `sections.s04_rating_evolution.largest_jump_tcc = -0.015`. With rule movement bounded
  by 0.001 in magnitude, at least `derived: -0.015 + 0.001 = -0.014` of that move falls
  into the *boat movement* remainder of the decomposition above. By construction that
  remainder is "everything that is not the stable-cohort mean"; whether her measurements
  actually changed is not recorded in the bundle.
<!-- /worked-example -->

<!-- worked-example boat=kestrel -->
**KESTREL — 17-year window, constant TCC.**
`sections.s09_formula_drift.window_years = 17` with no drift observed;
`sections.s04_rating_evolution.first_snapshot_tcc = 1.02` equals
`sections.s04_rating_evolution.latest_snapshot_tcc = 1.02` and
`sections.s04_rating_evolution.total_movement = 0.0`, so there is nothing to
decompose between rule and boat.
<!-- /worked-example -->

<!-- worked-example boat=chilli_pepper -->
**CHILLI PEPPER — 17-year window, no drift observed.**
`sections.s09_formula_drift.window_years = 17`; the bundle records
`drift_observed` false and an empty affected-measurement list for the Sunfast 3300
class, so no rule movement is attributed to her rating.
<!-- /worked-example -->

## Δ TCC to seconds per hour

### Definition

The product expresses every rating difference in the unit sailors feel: **seconds
of corrected time per hour of racing**, signed. Positive ΔTCC = rated faster = owes
time = loses seconds.

### Formula

IRC corrected time = elapsed time × TCC. For one hour (3600 s) of elapsed time, a
TCC difference of 0.001 changes corrected time by 3600 × 0.001 = 3.6 s.

```
sec_per_hour = ΔTCC / 0.001 × 3.6          (= ΔTCC × 3600)
```

One TCC "point" is 0.001 (the third decimal of a certificate); one point ≈
**3.6 s/hr**. The result is signed (negative = the change helps) and rounded to one
decimal place when published (`round(sec_per_hour, 1)`); `delta_tcc` is rounded to
five decimals.

### Inputs

Any ΔTCC: a what-if estimate, a recommendation, a boat-vs-class-median gap, a rule
movement.

### Thresholds (below which the output is withheld)

None for the conversion itself. Whether the *input* ΔTCC is published is governed by
the component that produced it (class regression, what-if estimator). The conversion
is on **corrected time at a constant one-hour elapsed basis**; it is linear and does
not model course type, wind or non-time-on-time scoring (PHS, ORC time-on-distance).

### Versioning rule

`SECONDS_PER_HOUR_PER_TCC_POINT = 3.6` is part of the `WhatIfEstimateV1` contract.
It follows from the IRC time-on-time definition; changing it would be a methodology
change and a contract version bump.

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL's gap to the class median.** `sections.s01_executive.tcc_now = 1.02`
against `sections.s01_executive.class_median_tcc = 1.015` is
`derived: 1.02 - 1.015 = 0.005` TCC, i.e.
`derived: (1.02 - 1.015) / 0.001 * 3.6 = 18` s/hr of time owed relative to the class
median boat.
<!-- /worked-example -->

<!-- worked-example boat=chilli_pepper -->
**CHILLI PEPPER's gap to the class median.** `sections.s01_executive.tcc_now = 1.026`
against `sections.s01_executive.class_median_tcc = 1.008` is
`derived: (1.026 - 1.008) / 0.001 * 3.6 = 64.8` s/hr of time owed relative to the
class median boat.
<!-- /worked-example -->

<!-- worked-example boat=diablo_j -->
**DIABLO-J's certificate change.** A move from
`sections.s04_rating_evolution.first_snapshot_tcc = 1.011` to
`sections.s04_rating_evolution.latest_snapshot_tcc = 0.996` is
`derived: (0.996 - 1.011) / 0.001 * 3.6 = -54` s/hr: she owes 54 seconds per hour
less than on the 2009 certificate.
<!-- /worked-example -->

## One-design and "not meaningful"

### Definition

Where a number would be misleading — a class with no rating spread to explain, a class
too small to fit, a split with too few races — the product says so and withholds the
number instead of fabricating a table. Two distinct situations are covered:

1. **One-design / low-headroom class:** the class's TCCs are (almost) identical, so
   there is nothing for a regression to explain and no rating to gain by changing
   measurements.
2. **Below minimum N:** too few boats or races to say anything (the **Cape 31** case
   below).

### Formula

* **One-design flag (report path).** `tcc_spread = max(TCC) − min(TCC)` over the
  class's latest snapshots; `is_one_design = tcc_spread < 0.0010`. When set, the
  interpretation is suffixed "[Strict One-Design / Low-Headroom Class: Continuous
  physical measurement sliders are suppressed.]"
* **Low-headroom class (comparator).** `cv = SD(TCC) / mean(TCC)`;
  `cv < 0.005` → "low (one-design-like)", `cv < 0.015` → "moderate", else "high
  (significant within-class variation)". `headroom_to_best = max(TCC) − median(TCC)`.
* **Below minimum N.** Class regression: see [Class regression](#class-regression)
  (`N < 5` → withheld). Race-prep condition splits: a bucket with fewer than
  `MIN_SPLIT_RACES = 3` races is `meaningful = False` and the condition-fit signal is
  `insufficient_data`, never a fabricated read.

### Inputs

Latest TCC snapshot per boat in the class; race counts per split bucket.

### Thresholds (below which the output is withheld)

| Rule | Value | Constant | Effect |
|---|---|---|---|
| One-design TCC spread | < 0.0010 | inline | `is_one_design = true`; sliders suppressed. |
| Comparator low headroom | CV < 0.005 | `_HEADROOM_LOW_CV` | "low (one-design-like)". |
| Comparator moderate headroom | CV < 0.015 | `_HEADROOM_MODERATE_CV` | "moderate". |
| Class regression minimum N | 5 | `MIN_BOATS` | `WithheldClassResult`. |
| Race-prep split bucket | 3 races | `MIN_SPLIT_RACES` | Bucket not meaningful. |
| Condition-fit signal strength | RAI gap ≥ 5 moderate, ≥ 10 strong | `_SIGNAL_MODERATE_DELTA`, `_SIGNAL_STRONG_DELTA` | Between two meaningful buckets. |

### Versioning rule

The one-design threshold and the headroom CVs are methodology constants: changing
them is a methodology change. `WITHHELD_FIXTURE_CLASS = "Cape 31"` in
`class_regression.py` is the canonical withheld example used by tests.

### Worked example

The golden bundles show the **negative** case: none of the three classes is one-design.

<!-- worked-example boat=kestrel -->
**KESTREL — Sunfast 3300.** `sections.s05_class_context.class_tcc_min = 0.911` and
`sections.s05_class_context.class_tcc_max = 1.029` over
`sections.s05_class_context.class_n = 35` boats give a spread of
`derived: 1.029 - 0.911 = 0.118`, far above the one-design threshold, so the report
path leaves `is_one_design` false and the sensitivity table is published.
<!-- /worked-example -->

<!-- worked-example boat=diablo_j -->
**DIABLO-J — J/92.** `sections.s05_class_context.class_tcc_min = 0.958` and
`sections.s05_class_context.class_tcc_max = 1.192` over
`sections.s05_class_context.class_n = 4` boats: a spread of
`derived: 1.192 - 0.958 = 0.234`. This class is not one-design; its regression is
withheld purely on N (see [Class regression](#class-regression)).
<!-- /worked-example -->

### Fixture example — Cape 31 (not a golden bundle; figures from the test fixtures)

No golden boat is a Cape 31, so this example uses the test fixtures that define the
rule; these figures are *not* machine-checked against a golden bundle.

* **Class regression.** The `tests/test_class_regression_sm_01_02.py` fixture
  `_cape31_rows()` has 3 boats at TCC ≈ 1.090 (noise SD 0.005) against
  `MIN_BOATS = 5`, so `regress_class_rows` returns a `WithheldClassResult` with
  reason `below threshold: N=3 < MIN_BOATS=5`. No coefficient, R² or table is
  produced.
* **Comparator.** The `tests/test_comparative_metrics.py` fixture has four Cape 31
  boats all at TCC 1.000: spread 0, CV 0 < 0.005, so modification headroom is
  "low (one-design-like)" and `headroom_to_best` is 0.0 — nothing to gain from
  changing measurements — whereas the diverse Sunfast 3300 fixture reads "moderate"
  or "high" with a 0.020 headroom from the class median (1.025) to the class
  maximum (1.045).

## What-if estimator

### Definition

Given proposed changes to levers an owner controls (declared headsails and
spinnakers, crew number, draft, displacement, …), an *estimate* of the resulting
ΔTCC, its seconds-per-hour equivalent and an indicative uncertainty band, ranked
into recommendations by impact × feasibility × evidence.

**Disclaimer (mandatory on every estimate, recommendation and trial-certificate
payload):** *"estimate from class regression — not an official rating"*
(`ESTIMATE_DISCLAIMER`), with the machine-readable flag
`class_regression_estimate` (`ESTIMATE_FLAG`). The estimator never claims to know
the IRC formula; only the rating office can issue a rating, and a trial
certificate is the route to an official number.

### Formula

For each lever: `applied_Δ` = requested change clamped to class-legal bounds
(`min_legal`, `max_legal`, `max_step_down`, `max_step_up`, optional caller class
bounds; declaration levers rounded to integers), then `lever_ΔTCC = applied_Δ × raw_β`,
where `raw_β = beta_per_unit / unit_scale` from the report-path class regression
(Tier A, B or C of the boat's class).

```
naive_Δ        = Σ lever_ΔTCC
combination    = 2/3 if two or more levers move, else 1
ΔTCC           = naive_Δ × combination                (COMBINATION_FACTOR = 2/3)
evidence_noise = 1 − min(|std_beta|, 1)               (per lever)
variance_term  = (|lever_ΔTCC| × (0.5 + evidence_noise))²
model_noise    = max(0.35, 1 − R²)
σ              = sqrt(Σ variance_term) × model_noise × combination
half_width     = max(σ, 0.0005)
interval       = [ΔTCC − half_width, ΔTCC + half_width]
estimated_TCC  = base_TCC + ΔTCC
sec_per_hour   = ΔTCC / 0.001 × 3.6
```

The interval is a **heuristic band, not a statistical confidence interval**: weaker
evidence and lower R² widen it, it is never narrower than ±0.0005 TCC, and the 0.35
floor means that for any model with R² above 0.65 the band is governed by the floor,
not by the fit. `COMBINATION_FACTOR` was calibrated against a single golden scenario
(headsail −0.004 + kite −0.003 + crew −0.002 summed to −0.009; observed ≈ −0.006).
Recommendations are ranked on impact × feasibility × evidence weight (`strong` 1.0,
`moderate` 0.7, `limited` 0.4). Evidence strength: Tier A → "strong" if
`|std_beta| > 0.3`, else "moderate"; Tier B → "moderate" if `> 0.3`, else "limited";
Tier C → always "limited".

### Inputs

The boat's class regression (coefficients, R², tier), the boat's current lever
values, optional class-legal bounds, optional cost overlay.

### Thresholds (below which the output is withheld)

* No class model (class withheld, or fewer than 5 boats): every lever has zero
  estimated effect; legal bounds are still enforced; the output is not a
  recommendation.
* One-design / low-headroom class: continuous measurement sliders are suppressed
  (see [One-design and "not meaningful"](#one-design-and-not-meaningful)).
* Levers whose feasibility is above 7 (hull-fixed) are never recommended
  (`RECOMMENDABLE_LEVERS`).
* Uncertainty half-width is never below 0.0005 TCC.

### Versioning rule

Contracts `WhatIfEstimateV1`, `RecommendationV1`, `TrialCertificateSuggestionV1`.
The disclaimer text, flag, conversion constant, combination factor, noise floor and
evidence weights are methodology constants: changing any is a methodology change and
needs a contract version bump.

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL — drop one declared headsail (Sunfast 3300, Tier A model).**

* From the class model:
  `engines.design_model.coefficients[field=headsails].beta_per_unit = 0.00208` TCC per
  sail, `engines.design_model.coefficients[field=headsails].std_beta = 0.0011` and
  `engines.design_model.r_squared = 0.9942`. She declares
  `sections.s07_sensitivity.coefficients[field=headsails].this_boat = 2.0` headsails
  against a class mean of
  `sections.s07_sensitivity.coefficients[field=headsails].class_mean = 2.333`.
* Single lever, so `combination` is 1. `delta_tcc` is `derived: -1 * 0.00208 = -0.00208`
  TCC, which is `derived: -0.00208 / 0.001 * 3.6 = -7.488` s/hr (published rounded to
  one decimal as −7.5).
* Uncertainty: the `model_noise` term is the 0.35 floor (1 − R² is far below it) and
  `derived: 1 - 0.0011 = 0.9989` is the lever's evidence noise, so
  `derived: 0.00208 * (0.5 + 1 - 0.0011) * 0.35 = 0.0010912` is the half-width in TCC.
  The band is `derived: -0.00208 - 0.0010912 = -0.0031712` to
  `derived: -0.00208 + 0.0010912 = -0.0009888` TCC.
* Every part of that response carries the disclaimer: *estimate from class
  regression — not an official rating*. The golden bundle's own recommendation list
  is empty, so this is a worked calculation, not a recorded output.
* Caution carried with the number: `engines.design_model.r_squared_cv = 0.5618` shows
  the class model does not predict held-out boats as well as its in-sample R² suggests,
  and the headsail lever has one of the smallest standardised effects in the class.
<!-- /worked-example -->

## Comparative metrics and race preparation

### Definition

Supporting comparisons that appear next to the headline numbers.

* **Head-to-head** (`HeadToHeadV1`, rivals): wins/losses between two boats across
  shared races, "uncorrected" by published place and "corrected" by corrected
  outcome. Real corrected times from `raw_data` (`irc_corrected`, `corrected`,
  `corrected_time`, `phs_corrected`; elapsed from `elapsed`, `elapsed_time`,
  `finish_time`) are preferred; otherwise the proxy `place / rating_value`
  (lower = sailed better relative to rating) is used. Minimum shared meetings for a
  rival: 2 (`DEFAULT_MIN_RIVAL_MEETINGS`).
* **Design comparator** (`DesignComparatorV1`): TCC band (mean ± 0.02), min / max /
  mean / median / SD TCC, mean and median RAI across the class, results depth, and
  modification headroom (see [One-design and "not meaningful"](#one-design-and-not-meaningful)).
* **Fleet summary** (`FleetSummaryV1`): boats, designs, countries, TCC distribution
  and activity aggregates.
* **Race preparation** (`RacePrepFactsV1`): per-condition RAI splits by fleet size
  (`< 8`, `8–20`, `> 20` boats) and course distance (`≤ 10`, `11–30`, `> 30` nm),
  surfacing the split with the largest RAI gap between two meaningful buckets.

### Formula, inputs, thresholds

Head-to-head counts use only races where both boats have a finishing position
and shared event date. Split buckets and the signal thresholds are in the table under
[One-design and "not meaningful"](#one-design-and-not-meaningful). Race counts use
the same `BASIC_IRC_FILTER` as RAI so the numbers reconcile.

### Versioning rule

Contract ids as in the index table (`HeadToHeadV1`, `DesignComparatorV1`,
`FleetSummaryV1`, `RacePrepFactsV1`). Bands and thresholds are methodology constants.

### Worked example

<!-- worked-example boat=kestrel -->
**KESTREL's head-to-head against BELLINO.**
`sections.s06_performance.head_to_head[name=BELLINO].head_to_head_wins = 1` win to
`sections.s06_performance.head_to_head[name=BELLINO].head_to_head_losses = 24`
losses in shared races, with
`sections.s06_performance.head_to_head[name=BELLINO].recent_finishes_count = 19`
recent finishes recorded for BELLINO; BELLINO's current rating is
`sections.s06_performance.head_to_head[name=BELLINO].tcc = 1.023`.
<!-- /worked-example -->

## Backtesting and golden tolerances

### Definition

Model changes are tested like code. The three golden boats must reproduce every
figure of their `ReportFactsV1` bundle within tolerance, RAI must stay stable on
held-out seasons, and the rating model must predict a held-out 20 % of boats.

### Formula and thresholds

| Gate | Tolerance | Constant |
|---|---|---|
| Golden figures | absolute 5e-3 or relative 1e-3 | `DEFAULT_ABS_TOL`, `DEFAULT_REL_TOL` |
| RAI held-out-season stability | ≤ 7.5 RAI points between full-history and hold-one-season-out RAI | `RAI_STABILITY_TOL` |
| Minimum races after holding a season out | 5 | `RAI_MIN_RACES_AFTER_HOLDOUT` |
| Rating-model holdout (Tier C), deterministic 80/20 split, seed 42 | MAE ≤ 0.040 and R² ≥ 0.80 | `RATING_MODEL_HOLDOUT_MAE_MAX`, `RATING_MODEL_HOLDOUT_R2_MIN` |

### Inputs

`dataset.json` and `golden_report_facts_v1.json` per golden boat; run with
`python -m pytest tests/report -m model_regression -q` from `api/`. The evaluation
report is `api/test-results/sm_01_08_eval_report.json`.

### Versioning rule

A moved golden figure is either a regression (fix the code) or an intentional
methodology change (re-snapshot with `api/scripts/sm_01_08_build_golden.py`, review the
diff, update this document — the worked examples will fail until you do). Strings in
`sections/s11_appendix.py` and the facts builders are echoed into the golden
bundles; editing them re-snapshots the bundles and so is itself a methodology change.

### Worked example

<!-- worked-example boat=diablo_j -->
**DIABLO-J — long-history fixture.** The fixture has
`engines.rai.n_races = 199` races, which is what allows RAI to be recomputed with one
season held out and still clear `RAI_MIN_RACES_AFTER_HOLDOUT`. Her full-history
`engines.rai.rai = 26.58` must stay within `RAI_STABILITY_TOL` of the
hold-one-season-out value.
<!-- /worked-example -->

## Open decisions for Stuart

These are places where the code, as merged, contains two definitions or an unstated
policy. They are recorded here because the methodology cannot be approved
without a decision; none is changed by this card (the card is documentation only and
must not alter any string or code that feeds the golden bundles).

1. **RAI field ordering.** The report path (`performance.compute_rai`, used by every
   golden bundle) treats a *higher* TCC as faster and expects it to finish better
   (`rank = 1 + #(ratings above)`). The API path (`rai.py`) ranks the other way
   (`rank = 1 + #(ratings below)`, "lowest-rated boat is expected to win"). The two
   give different expected percentiles for the same race. Decide which is the
   definition, and whether the report moves to `RAIComputationV1`.
2. **RAI interval method and minimum races.** The report path uses a Student-t
   interval with no minimum-race withholding (a boat with one race gets a number and,
   below 3 races, a zero-width interval); the API path uses a pinned-seed bootstrap-t
   and withholds below 5 races. Decide whether the 5-race threshold applies to the
   report.
3. **Regression coefficient scaling.** The report path's `std_beta` is TCC per SD of
   lever (target not standardised); the API path's `std_beta` is SD-of-TCC per SD of
   lever. Same name, different numbers; prose must not compare them.
4. **No R² / cross-validation floor.** A class can publish with in-sample R² 0.9778
   and cross-validated R² 0.1661 (CHILLI PEPPER). Decide whether a CV-R² threshold
   should withhold or downgrade the lever ranking.
5. **Smart-boat cohort definition.** Report path: top 10 % by finishing percentile
   with at least 3 races (and an empty cohort when the performance view is absent).
   API path: lowest-TCC quintile. These are different populations with the same name.
6. **Heuristic what-if band.** The uncertainty interval is a documented heuristic with a
   0.35 noise floor and a single-scenario calibration of the combination factor, not
   a statistical interval. Decide whether it may be labelled an "indicative range" in
   the UI or needs empirical calibration.
7. **Glossary coverage.** `docs/ai/glossary.yaml` (AI-01-01) does not yet define RAI,
   standardised β, model tiers, smart boat, stable-certificate cohort or
   headroom; see [GLOSSARY.md](GLOSSARY.md) for the proposed definitions.
