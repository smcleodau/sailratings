# Explainable Match Scoring (DP-04-03)

> Rank identity evidence **without opaque magic**.
>
> **Code of record:** `api/src/irc_data/matching/scoring.py`
> (`SCHEMA_VERSION = "scorer-v1"`, features `scorer-rules-v1`,
> threshold schema `threshold-config-v1`).
> **Builds on:** DP-04-02 (`CandidatePair` — every scored pair is explained
> by ≥1 blocking rule), DP-03-03 (normalised `EntityObservation` input).
> **Consumed by:** DP-04-04 (auto-resolution policy) and DP-04-05
> (adjudication — the uncertain band routes to humans).
> **Verification:** `api/tests/matching/test_scoring.py` and the
> holdout-evaluation harness `api/scripts/verify_dp_04_03.py`.

---

## 1. Purpose

DP-04-02's blocking reduces the quadratic comparison space to a small set of
candidate pairs, each already explained by ≥1 blocking rule.  DP-04-03 owns
the next step: turn each candidate pair into a **score** that ranks how
strong the identity evidence is — and does it in a way a human (or the
MatchCard) can *audit line by line*.

Three properties are enforced on the contract, not by convention:

1. **Reproducible.**  A score is a pure function of the two observations,
   the pair's blocking provenance, the versioned feature ruleset and the
   versioned threshold config.  No clocks, no randomness, no hidden state.
   Re-running the scorer on unchanged input reproduces the identical score
   *and* the identical explanation (the config carries a fingerprint so runs
   are comparable).
2. **Explainable.**  The score is a weighted sum of **named** features.
   Every scored pair carries the full `FeatureContribution` vector —
   `weight × value = points` per feature — plus the list of features that
   were **missing**.  Nothing is imputed: absence is *preserved* as
   missingness and contributes exactly 0 points.
3. **Calibrated by entity type.**  `fit_thresholds` fits the
   *uncertain band* (`auto_reject_below` … `auto_merge_at_or_above`) on
   **labelled** examples per entity type.  The band is what DP-04-05 routes
   to human adjudication; the calibrated band carries the fingerprint of the
   labelled set it was fit on.

## 2. Score construction (`scorer-rules-v1`)

```
score = Σ (feature_weight × feature_value)          ∈ [0, 1]

feature_value ∈ [0, 1]  for present features
feature_value = 0       for missing features (never imputed)
```

Because every weight is non-negative and every value lies in `[0, 1]`, and
the eleven weights sum to exactly `1.0`, the score is **bounded by
construction**.  Every feature can only *add* evidence — there is no
negative evidence and no hidden penalty term, so the score is auditable as a
plain sum of parts.

| id  | name                     | weight | signal |
|-----|--------------------------|--------|--------|
| F01 | `sail_exact`             | 0.22   | shared strong sail token (short bare numerics excluded as ambiguous) |
| F02 | `registry_exact`         | 0.20   | normalised registry / hull id equal |
| F03 | `name_similarity`        | 0.14   | `1 − levenshtein/max_len` over normalised name keys |
| F04 | `design_exact`           | 0.08   | design families equal (punctuation/spacing collapsed) |
| F05 | `country_match`          | 0.04   | flag country equal |
| F06 | `loa_closeness`          | 0.06   | `1 − rel|ΔLOA|/0.20` (taper) |
| F07 | `year_closeness`         | 0.05   | `1 − |Δyear|/10` (taper) |
| F08 | `blocking_corroboration` | 0.06   | `0 / 0.5 / 1.0` for `1 / 2 / ≥3` blocking rules fired |
| F09 | `name_token_jaccard`     | 0.05   | jaccard over significant name tokens (stopwords removed) |
| F10 | `beam_closeness`         | 0.03   | `1 − rel|Δbeam|/0.20` (taper) |
| F11 | `temporal_overlap`       | 0.07   | `1` if validity/build-year eras overlap else `0` |

The weights are **hand-set priors**, deliberately simple, and *not* magic:
the holdout evaluation (§5) measures the precision / recall / calibration
they actually achieve, and the per-entity-type **thresholds** are fit to
labelled data rather than guessed.  Changing any feature or weight ships
`scorer-rules-v2` alongside v1 so prior scores remain reproducible.

### Optional model blend

An external learned score (e.g. a dedup model) can be blended via

```
score = (1 − λ) · deterministic + λ · model_score        λ ≤ MAX_MODEL_WEIGHT
```

with the **deterministic floor intact** (`model_weight` defaults to `0.0` and
is hard-capped at `0.5`).  A fully-corroborated pair reaches the auto-merge
band on deterministic evidence *alone* (`AUTO_MERGE_FLOOR = 0.90`), so the
pipeline never *needs* the model — it is an optional refinement, never a
black box that can override the evidence.

## 3. The output contract: `ScoredPairV1`

```python
ScoredPairV1(
    pair=CandidatePair(...),            # DP-04-02 provenance (rules_fired, matching_keys)
    entity_type="boat",
    deterministic_score=0.56,
    model_score=None, model_weight=0.0,
    score=0.56,
    feature_contributions=(FeatureContribution("F01", "sail_exact", 0.22, 1.0, 0.22, False, "..."), …),
    missing_features=("beam_closeness", "temporal_overlap"),
    thresholds=ThresholdConfig("boat", 0.22, 0.398, fit_pairs=14, fit_fingerprint="d63f27e4…"),
)
```

Everything needed to *explain* the score is on the contract: the DP-04-02
pair provenance, the full feature vector with per-feature points, the
missing-feature list, the optional blended model score, and the threshold
snapshot the pair was routed with.  `to_scored_candidate_kwargs()` maps a
`ScoredPairV1` straight onto the DP-04-05 `ScoredCandidateV1` input contract
(carrying `score` + `score_explanation`), so the MatchCard renders the exact
same line items.

## 4. Threshold calibration by entity type

`fit_thresholds(labelled, entity_type=…)` calibrates the band on labelled
examples.  It is deliberately **conservative** — a wrong merge is far more
expensive than a pair sent to adjudication:

* `auto_reject_below` — the highest value that keeps **recall** (matches
  kept at-or-above the reject line) ≥ `min_recall`;
* `auto_merge_at_or_above` — the lowest value that yields at most
  `max_false_merges` false merges overall **and** at most
  `max_high_cost_false_merges` high-cost false merges (both default to
  `0`).  With zero budgets the line sits strictly above the highest-scoring
  non-match in the calibration set.

The search is a pure function of the labelled scores, so the fitted band is
reproducible and carries the labelled set's fingerprint.

## 5. Holdout evaluation — measured evidence

`evaluate_holdout(holdout, thresholds, bins=10)` measures, on a split the
calibration never saw:

* **precision** / **recall** at the auto-merge line;
* **calibration** — 10 per-bin `mean_score` vs empirical match-rate rows and
  the expected calibration error (ECE);
* **high-cost false merges** — non-matches scored ≥ the merge line that were
  flagged high-cost (rated / has results / has certificate);
* **uncertain** — pairs in the band (routed to adjudication).

Measured evidence (from `api/scripts/verify_dp_04_03.py`, labelled corpus of
56 observations → 24 candidate pairs, 12 labelled matches + hard non-matches
incl. high-cost traps; 60/40 calibration/holdout split):

| metric | holdout value |
|--------|---------------|
| calibrated boat band | reject `< 0.220` ≤ uncertain `< 0.398` ≤ merge |
| precision | **1.000** |
| recall | **1.000** |
| expected calibration error | 0.299 |
| **high-cost false merges** | **0** |
| uncertain (→ human) | 2 / 10 |
| reproducibility | identical scores + metrics on re-run |

All 28 harness checks pass (`RESULT: PASS`); `pytest tests/matching` →
146 passed.

## 6. Uncertain band → adjudication

The calibrated band decides which pairs are *confident*; DP-04-05's
`AdjudicationQueue` then applies its own admission policy (auto-resolve only
confident **low-impact** candidates; queue everything uncertain or
high-impact).  The verified contract: **a pair the calibrated scorer calls
uncertain is never auto-resolved — it always reaches a human** (the queue
only ever queues *more*, never auto-resolves a calibrated-uncertain pair),
and a high-impact candidate is queued regardless of score.

## 7. Handoff summary

| Consumer | Reads | Guarantee |
|---|---|---|
| DP-04-04 auto-resolver | `ScoredPairV1.score`, `routing_band`, `thresholds` | score is reproducible; the band is calibrated per entity type |
| DP-04-05 adjudication | `to_scored_candidate_kwargs()` → `ScoredCandidateV1` | score + line-item `score_explanation`; uncertain pairs always reach a human |
| MatchCard UI (AD-01-04) | `feature_contributions`, `missing_features` | every point decomposes into named features; missingness is explicit |
| Identity consumers | `score ∈ [0,1]`, `deterministic_score` | bounded score; deterministic floor intact even when a model is blended |
| QA / verification | `fit_thresholds`, `evaluate_holdout`, `HoldoutMetrics` | precision / recall / calibration / high-cost false merges are reproducible |
| Future changes | `get_scorer_ruleset(id)`, `KNOWN_SCORER_RULESETS` | feature rulesets are versioned; old scores stay reproducible |

### Acceptance-criteria traceability

* *"Combine deterministic features and optional model score"* → §2
  (11 deterministic features + `model_score` blend with `model_weight`).
* *"calibrate thresholds by entity type"* → §4 (`fit_thresholds` per
  `entity_type`, carrying the fit fingerprint).
* *"preserve feature contributions and missingness"* → §3
  (`feature_contributions` sum to the score; `missing_features` recorded,
  never imputed).
* *"Scores are reproducible and calibrated on labelled examples"* → §5
  (identical re-run; band fit on the labelled calibration split).
* *"uncertain band routes to adjudication"* → §6 (DP-04-05 integration).
* *"Holdout evaluation reports precision, recall, calibration and high-cost
  false merges"* → §5 (`evaluate_holdout` → `HoldoutMetrics`).
