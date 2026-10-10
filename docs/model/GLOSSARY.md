# SailRatings model glossary

Companion to [METHODOLOGY.md](METHODOLOGY.md) (`ModelMethodologyV1`, version 1.0.0,
2026-10-10). Status: **DRAFT — awaiting Stuart's approval.**

Sailing and rating terms are **defined once**, in `docs/ai/glossary.yaml` (card
AI-01-01), and are cross-linked here **by exact term name** — this file does not
restate those definitions, so the two cannot drift apart. Terms that only exist
because of the model are defined in the second section; they are proposed for
addition to `glossary.yaml` (that file is owned by AI-01-01 and is not edited here).

`api/tests/report/test_methodology_doc.py` checks that every term in the first
table exists in `docs/ai/glossary.yaml` by its `term` name, and that no term in the
second section has since been added there (if one has, move it to the first table).

## Terms defined in docs/ai/glossary.yaml

| glossary.yaml term | Where the model uses it | Methodology section |
|---|---|---|
| `TCC` | Target of every class regression; the quantity RAI ranks; the unit of ΔTCC. | [Class regression](METHODOLOGY.md#class-regression) |
| `Non-spinnaker TCC` | Not modelled; RAI's race filter keeps IRC-rated rows only. | [Racing Advantage Index](METHODOLOGY.md#racing-advantage-index-rai) |
| `IRC` | Rating system of the report path; formula confidential, so all coefficients are "consistent with". | [Class regression](METHODOLOGY.md#class-regression) |
| `ORC` | Separately regressed rating system (targets GPH and triple numbers); never pooled with IRC. | [Class regression](METHODOLOGY.md#class-regression) |
| `GPH` | ORC regression target. | [Class regression](METHODOLOGY.md#class-regression) |
| `Triple Number` | ORC regression targets `triple_low`, `triple_med`, `triple_high`. | [Class regression](METHODOLOGY.md#class-regression) |
| `Elapsed time` | Basis of the seconds-per-hour convention. | [Δ TCC to seconds per hour](METHODOLOGY.md#δ-tcc-to-seconds-per-hour) |
| `Corrected time` | The result RAI's `actual_pct` is taken from; head-to-head "corrected" outcome. | [Racing Advantage Index](METHODOLOGY.md#racing-advantage-index-rai) |
| `Time-on-time` | Why 0.001 TCC = 3.6 s per hour. | [Δ TCC to seconds per hour](METHODOLOGY.md#δ-tcc-to-seconds-per-hour) |
| `Time-on-distance` | Scoring not modelled by the seconds-per-hour convention. | [Δ TCC to seconds per hour](METHODOLOGY.md#δ-tcc-to-seconds-per-hour) |
| `Point` | One point = 0.001 TCC = 3.6 s/hr; the conversion constant. | [Δ TCC to seconds per hour](METHODOLOGY.md#δ-tcc-to-seconds-per-hour) |
| `TWS` | Basis of the RAI condition bands (light, medium, fresh, heavy). | [Racing Advantage Index](METHODOLOGY.md#racing-advantage-index-rai) |
| `Drift` | The quantity rule drift measures (a change in the measurement–TCC relationship over time). | [Rule drift](METHODOLOGY.md#rule-drift) |
| `One-design` | Class type for which the low-headroom rule suppresses sliders. | [One-design and "not meaningful"](METHODOLOGY.md#one-design-and-not-meaningful) |
| `Class rules` | Source of the class-legal bounds the what-if estimator enforces. | [What-if estimator](METHODOLOGY.md#what-if-estimator) |
| `Trial certificate` | The route from a what-if estimate to an official number. | [What-if estimator](METHODOLOGY.md#what-if-estimator) |
| `Rule year` | The cycle a certificate was issued under; the unit of a rating cycle in rule drift. | [Rule drift](METHODOLOGY.md#rule-drift) |
| `Crew` | Tier B / C and API-path lever; what-if lever. | [Class regression](METHODOLOGY.md#class-regression) |
| `Headsails declared` | Tier A / B lever `headsails`; stable-cohort measurement; what-if lever. | [What-if estimator](METHODOLOGY.md#what-if-estimator) |
| `Symmetric spinnaker` | Context for the `spinnakers` lever (declared count). | [Class regression](METHODOLOGY.md#class-regression) |
| `LH` | Lever `lh`. | [Class regression](METHODOLOGY.md#class-regression) |
| `Beam` | Lever `beam` (Tier B / C). | [Class regression](METHODOLOGY.md#class-regression) |
| `Draft` | Lever `draft`. | [Class regression](METHODOLOGY.md#class-regression) |
| `Displacement` | Lever `displacement`. | [Class regression](METHODOLOGY.md#class-regression) |
| `DLR` | Lever `dlr` (Tier B / C). | [Class regression](METHODOLOGY.md#class-regression) |
| `HSA` | API-path IRC lever `hsa`. | [Class regression](METHODOLOGY.md#class-regression) |
| `SPA` | API-path IRC lever `spa`. | [Class regression](METHODOLOGY.md#class-regression) |
| `P` | Tier A lever `p`. | [Class regression](METHODOLOGY.md#class-regression) |
| `E` | Tier A lever `e`. | [Class regression](METHODOLOGY.md#class-regression) |
| `J` | Tier A lever `j`. | [Class regression](METHODOLOGY.md#class-regression) |
| `HLU` | Tier A lever `hlu`. | [Class regression](METHODOLOGY.md#class-regression) |
| `HLP` | Tier A lever `hlp`. | [Class regression](METHODOLOGY.md#class-regression) |
| `MUW` | Tier A lever `muw`. | [Class regression](METHODOLOGY.md#class-regression) |
| `MHW` | Tier A lever `mhw` (the strongest lever for the Sunfast 3300 golden boats). | [Class regression](METHODOLOGY.md#class-regression) |

## Model terms not yet in docs/ai/glossary.yaml

Proposed definitions. Each is the definition used in [METHODOLOGY.md](METHODOLOGY.md);
if you change one, change it there and bump the methodology version.

**RAI (Racing Advantage Index).** The mean over a boat's races of
`(expected finish percentile − actual finish percentile) × 100`. Positive means the
boat finishes better than her rating predicts. Always published with a 95 % CI and a
race count. Contract `RAIComputationV1` (`rai-config-v1`).
→ [Racing Advantage Index](METHODOLOGY.md#racing-advantage-index-rai)

**Expected finish percentile.** A boat's TCC rank among the ratings in a race, divided
by the field size. The two code paths rank in opposite directions
(see [Open decisions](METHODOLOGY.md#open-decisions-for-stuart)).

**Actual finish percentile.** `place / fleet_size` on the official corrected result.

**Standardised β (`std_beta`).** A regression coefficient on a standardised lever:
the report path expresses it as ΔTCC per 1 SD of the lever; the API path as SD of TCC
per SD of the lever. Used to rank levers; not comparable across the two paths.
→ [Class regression](METHODOLOGY.md#class-regression)

**β per unit (`beta_per_unit`).** The coefficient converted to a display unit
(`per 0.1m`, `per sail`, `per 100kg`, `per person`).

**R².** Fraction of the class's TCC variance the fitted model explains, in-sample.
**Cross-validated R² (`r_squared_cv`)** is the same measured on held-out boats; a
large gap between the two means the lever ranking should not be presented as stable.

**Model tier (A / B / C / `correlation_only`).** A = certificate measurements (15
levers); B = snapshot fields only (7 levers); C = the B levers fitted across all
designs ("All Designs (Fleet-Wide)"); `correlation_only` = class below the minimum
size, pairwise correlations only.

**Withheld / `insufficient_data`.** The explicit status returned instead of a number
when the evidence is below a threshold. Never a blank and never a fabricated value.
→ [One-design and "not meaningful"](METHODOLOGY.md#one-design-and-not-meaningful)

**Smart boat / smart-boat cohort.** The reference group of top performers in a class
(report path: top 10 % by finishing percentile with at least 3 races; API path:
lowest-TCC quintile). → [Smart-boat cohort](METHODOLOGY.md#smart-boat-cohort)

**Stable-certificate cohort.** Consecutive snapshot pairs of a boat whose `lh`, `beam`,
`draft`, `headsails` and `spinnakers` are unchanged; any TCC movement across them is
read as rule movement. → [Rule drift](METHODOLOGY.md#rule-drift)

**Rule movement / boat movement.** The decomposition of a boat's total TCC change into
the stable-cohort mean drift (rule movement) and the remainder (boat movement).

**Low headroom / one-design-like.** A class whose TCC spread (< 0.0010) or coefficient
of variation (< 0.005) is so small that changing measurements offers nothing to gain;
sliders are suppressed. **Headroom to best** is `max(TCC) − median(TCC)` for the class.

**Seconds per hour (`sec_per_hour`).** `ΔTCC / 0.001 × 3.6`; signed seconds of
corrected time per hour of racing. → [Δ TCC to seconds per hour](METHODOLOGY.md#δ-tcc-to-seconds-per-hour)

**Estimate flag (`class_regression_estimate`) and disclaimer.** The machine-readable
flag and the mandatory text *"estimate from class regression — not an official
rating"* carried on every what-if output. → [What-if estimator](METHODOLOGY.md#what-if-estimator)

**Collinearity warning.** A notice attached to a regression when two levers correlate
at `|r| > 0.9`, meaning their individual coefficients cannot be separated reliably.

**Dataset fingerprint / dataset version / config fingerprint.** Content hashes that tie
a published number to the exact input rows and ruleset that produced it.

**Golden bundle.** The checked-in `ReportFactsV1` JSON for a fixture boat (CHILLI
PEPPER, DIABLO-J, KESTREL) that the model must reproduce within tolerance.
→ [Backtesting and golden tolerances](METHODOLOGY.md#backtesting-and-golden-tolerances)
