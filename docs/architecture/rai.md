# Racing Advantage Index with Confidence Intervals (SM-01-03)

> *"Is this boat out-performing her rating?"* as a calibrated index.
>
> **Code of record:** `api/src/irc_data/analysis/rai.py`
> (`RAI_SCHEMA_VERSION = "RAIComputationV1"`, config schema
> `rai-config-v1`).
> **Builds on:** SM-01-01 / DP-04-04 (results resolved onto canonical boat
> identities — RAI is computed per resolved `boat_id`) and the shared
> analytics race filter (`irc_data.analysis.filters.BASIC_IRC_FILTER`) so
> the numbers line up with the other analytics engines.
> **Consumed by:** the analytics API surface (boat profile / fleet
> intelligence) and the report facts builders.
> **Verification:** `api/tests/test_rai.py` (golden fixtures) and the
> acceptance harness `api/scripts/verify_sm_01_03.py`.

---

## 1. Purpose

RAI turns a boat's corrected race history into a single calibrated number —
plus the statistical honesty around it:

1. **RAI with a confidence interval, per boat.** Each race contributes one
   *advantage observation*; the index is the mean, with a bootstrap 95 % CI
   so a two-race hot streak never reads as proven performance.
2. **Class baselines.** Mean/median/quartile RAI across the boats of a
   design class — the reference population for "is *this* boat special?".
3. **Condition splits.** Wherever a source payload carries wind data, the
   index is additionally split by true-wind-speed band, so a light-air
   specialist shows as such instead of an unexplained average.
4. **Minimum-race threshold.** Below the threshold the contract returns
   `status="insufficient_data"` with `rai=None` — never a confident-looking
   number on thin evidence — and the boat is excluded from class baselines.
5. **Reproducible per dataset version.** Every result carries a
   `dataset_fingerprint` (hash over the input rows) and a
   `config_fingerprint` (hash over the versioned ruleset); a re-run on an
   unchanged dataset is bit-identical.

## 2. Index construction (`rai-config-v1`)

Per race `r` for boat `b`:

```
actual_pct_r    = place / fleet_size                    (official corrected placing)
expected_pct_r  = TCC_rank(b, field) / field_size
advantage_r     = (expected_pct_r − actual_pct_r) × 100

RAI_b           = mean(advantage_r)        over scored races
```

* **Corrected results.** The published placing is the corrected-time
  outcome; only `status='finished'`, placed, `fleet_size > 1`, IRC-rated,
  non-twilight rows enter the computation (shared `BASIC_IRC_FILTER`).
* **Expected percentile.** In IRC a *lower* TCC means the boat is owed time
  by the fleet, so the lowest-rated boat is expected to win:
  `rank = 1 + #(field TCC strictly below boat TCC)`, ties sharing the best
  rank (conservative). The field is the set of **distinct ratings in the
  race** — one per resolved identity, not one per result row — so duplicate
  or wrongly-merged rows cannot bias the expectation; an identity-merge
  error shows up *only* in the boat's own actual finishes, which is exactly
  what the merge-sensitivity verification measures.
* **Single-boat fields** carry no information: the race is kept on the
  record (`scored=False` in `race_contributions`) but excluded from the
  index.

### Confidence interval

```
n < 2                → degenerate interval at the point estimate
se ≈ 0 (all equal)   → percentile bootstrap (2000 resamples)
otherwise            → bootstrap-t (2000 resamples, seeded RNG)
```

The RNG seed is pinned (`BOOTSTRAP_SEED`), so the CI is a deterministic
function of the input data — the bootstrap adds no run-to-run noise. The
CI method used is reported on the contract (`ci_method`).

### Interpretation bands

* CI wholly above 0 → *out-performing her rating*;
* CI wholly below 0 → *under-performing*;
* CI spans 0 → *racing to her rating within noise*.

This is deliberately stricter than the sign of the point estimate: a
positive RAI whose CI spans zero is reported as noise, not skill.

## 3. The output contracts

### `RAIComputationV1` (per boat)

```python
RAIResultV1(
    schema="RAIComputationV1",
    boat_id=301, boat_name="HELD", sail_number="GBR101", design="J/99",
    status="ok",                      # or "insufficient_data"
    rai=0.0, ci_lower=0.0, ci_upper=0.0,
    ci_method="bootstrap-percentile", confidence_level=0.95,
    n_races=8, n_scored=8,
    avg_finish_pct=0.5, avg_expected_pct=0.5,
    wins=8, podiums=8,
    meets_min_races=True, min_races_required=5,
    condition_splits=(BandSplitV1("light", "0–8 kn", 4, 0.0, 0.0, 0.0, "ok"), …),
    n_wind_observed=8,
    dataset_fingerprint="67089f1bcd50a7a0",
    config_fingerprint="0aa0b24f694e3aa8",
    race_contributions=(…per-race place/expected/advantage/tws rows…),
    interpretation="Racing to her rating within noise (…)",
)
```

Everything needed to *audit* the index is on the contract: the per-race
contributions (place, expected percentile, advantage, TWS band), the
threshold state, both fingerprints, and the CI method.

### `ClassBaselineV1` (per design class)

```python
ClassBaselineV1(
    schema="ClassBaselineV1", design="J/99",
    n_boats=2, n_boats_total=3,        # total counts under-threshold boats
    mean_rai=0.0, median_rai=0.0, std_rai=0.0, p25_rai=0.0, p75_rai=0.0,
    min_races_required=5,
    dataset_fingerprint="…", config_fingerprint="…",
)
```

Only boats with `status="ok"` (≥ `min_races` scored races) enter the
statistics; under-threshold boats are counted in `n_boats_total` and
excluded from every aggregate.

### TWS condition splits

Band edges (knots, `[lo, hi)`): `light 0–8`, `medium 8–14`, `fresh 14–20`,
`heavy 20+`. TWS is extracted from the race's `raw_data` payload
(`tws`, `tws_kt`, `true_wind_speed`, `wind_speed`, …; values ≤ 0 or
> 60 kn treated as sensor noise). Absence is preserved: races without wind
data simply don't enter any band, and bands with fewer than
`min_band_races` (3) report `status="insufficient_data"` with `rai=None` —
never silently pooled into the headline number.

## 4. Reproducibility per dataset version

* `dataset_fingerprint` = SHA-256 (16 hex chars) over the sorted
  `observation_key`s of the input rows — the *dataset version* for the
  computation. Any row added, removed, or mutated ⇒ new fingerprint.
* `config_fingerprint` = SHA-256 over the versioned `RAIRulesetConfigV1`
  (thresholds, resamples, seed, band edges). Changing any rule ships
  `rai-config-v2` so prior numbers stay reproducible.
* The pure layer (`compute_rai_from_observations`,
  `class_baseline_from_results`) is engine-free; the DB bridge
  (`compute_rai_v1`, `class_baseline_v1`) only fetches rows. Golden tests
  assert the two layers agree bit-for-bit.

## 5. Verification — measured evidence

Golden fixture: HELD (TCC 1.000) beats CHASER (TCC 1.050) in all 8 races,
with 4 × 6 kn (light) + 4 × 16 kn (fresh) wind readings on HELD. Both
boats sail exactly *to* rating (pinned advantage 0.0 in every race — the
handicap predicts the finish order, so the loser is not under-performing).

From `PYTHONPATH=src python3 scripts/verify_sm_01_03.py` — 26/26 checks,
`RESULT: PASS`; `pytest tests/test_rai.py` → 31 passed:

| acceptance criterion | measured |
|---|---|
| RAI + CI per boat from corrected results | golden RAI 0.0 with zero-width CI; varied series ⇒ bootstrap-t CI `[−45.36, 45.36]` brackets the mean |
| class mean RAI | baseline mean = mean of members; under-threshold boat counted (`n_boats_total=3`) but excluded (`n_boats=2`) |
| TWS condition splits | light 4 races / fresh 4 races pinned; no-wind bands report `insufficient_data` |
| min-race threshold | 4 scored races ⇒ `insufficient_data`, `rai=None`; 5 ⇒ `ok` |
| reproducible per dataset version | bit-identical re-run (incl. serialised contract); row added ⇒ new fingerprint; config change ⇒ new fingerprint |
| sensitivity to identity-merge errors | merging CHASER's results into HELD moves RAI by the pinned −25.0 and changes the fingerprint; splitting restores both |

## 6. Handoff summary

| Consumer | Reads | Guarantee |
|---|---|---|
| Boat profile / reports | `RAIResultV1.rai`, CI, `interpretation` | CI-calibrated claim; thin data never passes as a number |
| Fleet intelligence | `ClassBaselineV1` | baseline statistics exclude under-threshold boats |
| Condition analysis | `condition_splits` | bands below `min_band_races` are explicit, never pooled |
| QA / cache invalidation | `dataset_fingerprint`, `config_fingerprint` | run identity = `(dataset, config)` fingerprint pair |
| Identity operations (DP-04-04) | `race_contributions`, merge-sensitivity harness | merge/split effects are attributable per race and measured |
| Future changes | `RAIRulesetConfigV1`, `RAI_CONFIG_SCHEMA` | rulesets are versioned; old numbers stay reproducible |

### Acceptance-criteria traceability

* *"RAI with confidence interval per boat from corrected results"* → §2, §3
  (`RAIComputationV1`; bootstrap CI; shared corrected-results filter).
* *"class mean RAI"* → §3 (`ClassBaselineV1`, threshold-passing boats only).
* *"condition splits by TWS band where wind data exists"* → §3
  (`condition_splits`; absence preserved).
* *"minimum-race threshold enforced"* → §3, §5 (boat- and band-level).
* *"reproducible per dataset version"* → §4, §5 (bit-identical re-run;
  fingerprint pair).
* *"Golden fixtures; sensitivity test to identity-merge errors"* → §5
  (pinned HELD/CHASER fixture; −25.0 merge-error shift and restore).
