# Deterministic Blocking & Candidate Generation (DP-04-02)

> Find plausible boat-identity matches at scale **without all-pairs
> comparison**.
>
> **Code of record:** `api/src/irc_data/matching/blocking.py`
> (`SCHEMA_VERSION = "blocking-v1"`, ruleset `blocking-rules-v1`).
> **Builds on:** DP-03-01 (canonical entity vocabulary), DP-03-03
> (normalised observations).
> **Verification:** `api/tests/matching/test_blocking.py` and the
> human-runnable evidence script `api/scripts/verify_dp_04_02.py`.

---

## 1. Purpose

Identity resolution over `n` observations has `n·(n−1)/2` unordered
pairs — at 100k observations, ~5 billion comparisons, almost all wasted
because real duplicate fractions are a fraction of a percent.  Blocking
partitions observations into buckets on strong shared signals and only
ever compares pairs that share a bucket, keeping **known-match recall**
high while reducing **candidate volume** by orders of magnitude.

DP-04-02 delivers exactly this: a *versioned* ruleset of deterministic
blocking rules, a `CandidateGenerator`, and an evaluation harness that
measures **recall**, **precision ceiling** and **runtime** against a
labelled corpus.

## 2. The input contract: `EntityObservation`

Blocking consumes normalised entity observations — the DP-03 pipeline's
output handed to identity resolution:

| field           | meaning                                             |
|-----------------|-----------------------------------------------------|
| `observation_id`| unique id of the observation (required)             |
| `sail_number`   | as-recorded sail number                             |
| `registry_id`   | registry / hull id (HIN, ORC ref, national reg, IMO)|
| `name`          | boat name                                           |
| `design`        | design / model string ("Sydney 38", "J/122")        |
| `country`       | flag country                                        |
| `loa_m`         | length overall, metres                              |
| `beam_m`        | beam, metres                                        |
| `displacement_kg`| displacement, kg                                   |
| `year_built`    | build year                                          |
| `valid_from`/`valid_to` | source-valid interval of the observation    |

Every field except `observation_id` is optional; a missing field simply
means the rules depending on it emit no keys.

## 3. The versioned ruleset `blocking-rules-v1`

Rules are pure functions of one observation — no clocks, no randomness —
so candidate generation is fully reproducible.  The ruleset is immutable
and versioned; changing the rules means shipping `blocking-rules-v2`
alongside v1 so prior runs remain reproducible.

| id  | name                      | key signal |
|-----|---------------------------|------------|
| R01 | `sail_number_token`       | every equivalent sail token (class/country-prefix variants via `normalize_sail_tokens`); bare short numeric sails are **country-guarded** |
| R02 | `registry_id`             | normalised registry / hull id |
| R03 | `design_exact`            | design family + most discriminating name/sail token (guarded) |
| R04 | `dimensions_band`         | design family + LOA banded to 0.5 m (guarded) |
| R05 | `name_exact`              | full normalised boat name |
| R06 | `name_soundex_geo`        | soundex of name tokens + country (guarded — geography) |
| R07 | `temporal_overlap_design` | design family + 5-year temporal era from validity/year-built (guarded) |

**Guarding.**  Weak keys (ambiguous short sails, generic design families,
dimension bands) only fire alongside a corroborating signal (country, a
discriminating name token) so recall is kept without flooding the
candidate set.

**Pathological blocks.**  A block larger than `max_block_size` (default
500) is skipped to bound worst-case volume; the skip is recorded in
`BlockingStats.skipped_oversized_blocks` so any recall loss from capping
is measurable.

## 4. The output contract: `CandidatePair`

```python
CandidatePair(
    left_id="obs-a", right_id="obs-b",
    rules_fired=("R01", "R05"),          # sorted rule ids — ALWAYS non-empty
    matching_keys=("R01:AUS4343", "R05:WILD OATS XI"),
    ruleset_id="blocking-rules-v1",
)
```

**Every candidate records which rules fired** (the acceptance criterion):
`rules_fired` is validated non-empty, and `matching_keys` lists the exact
shared block keys so any candidate can be audited back to the rule and
bucket that produced it.  `CandidateReport` adds the ruleset id, ruleset
fingerprint, and run statistics (`BlockingStats`) for provenance.

## 5. Evaluation harness

`evaluate_candidates(report, known_matches, targets=…)` measures:

* **recall** — fraction of labelled duplicate pairs present in the
  candidate set (a pair lost here can never be recovered downstream);
* **precision ceiling** — the best precision any downstream scorer could
  achieve on this candidate set (blocking *defines* the ceiling);
* **pair_ratio** — candidates ÷ all pairs (volume metric);
* **runtime** — generation time, checked against a budget.

`EvaluationTargets` carries the dataset-specific targets
(`min_recall`, `max_pair_ratio`, `max_runtime_seconds`);
`EvaluationResult.passed()` is the acceptance gate.

### Measured evidence (from `scripts/verify_dp_04_02.py`)

| corpus | observations | all pairs | candidates | recall | pair ratio | runtime |
|--------|--------------|-----------|------------|--------|------------|---------|
| hand-labelled (7 dup cases + 40 uniques) | 55  | 1,485     | 7      | 1.000 | 0.0047 | 0.002 s |
| production-scale (200 planted dupes)     | 2000 | 1,999,000 | 15,804 | 1.000 | 0.0079 | 0.095 s |

Both meet their targets: known-match recall = 1.0, candidate volume well
under the 1 % ceiling, runtime far under budget — with zero
all-pairs comparison.

## 6. Handoff summary

| Consumer | Reads | Guarantee |
|---|---|---|
| Pairwise scorer (DP-04-03) | `CandidatePair.rules_fired`, `matching_keys` | every candidate is explained by ≥1 rule |
| Evaluation / QA | `evaluate_candidates`, `EvaluationResult` | recall / precision ceiling / runtime are reproducible |
| Pipeline operators | `CandidateReport.stats` | oversized-block skips and per-rule volumes are observable |
| Future rule changes | `get_ruleset(id)`, `KNOWN_RULESETS` | rulesets are versioned; old runs stay reproducible |

### Acceptance-criteria traceability

* *"Generate candidates from sail number, registry ID, design,
  dimensions, names, geography and temporal overlap using versioned
  rules"* → §3 (`blocking-rules-v1`: R01 sail, R02 registry, R03 design,
  R04 dimensions, R05 name, R06 geography+name, R07 temporal overlap).
* *"Known-match recall and candidate volume meet dataset-specific
  targets"* → §5 evidence table (recall 1.0, pair ratio ≤ 0.008 at
  2 000-observation scale).
* *"Every candidate records which rules fired"* → §4
  (`CandidatePair.rules_fired`, validated non-empty).
* *"Evaluation corpus measures recall, precision ceiling and runtime"* →
  §5 harness + `tests/matching/test_blocking.py` +
  `scripts/verify_dp_04_02.py`.
