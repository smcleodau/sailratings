# SPEC — SM-01-08: Model backtesting and golden fixtures (ReportFactsV1)

> Goal: model changes are tested like code; report numbers are reproducible.

## 1. ReportFactsV1 — the output contract

`irc_data.api.services.report.facts_bundle` emits the **ReportFactsV1**
bundle — the single numeric substrate the narrative generator (AI-01-06)
consumes. No LLM calls happen here; every figure the prose may cite is in
this bundle.

```jsonc
{
  "schema_version": "ReportFactsV1",   // pinned; bumping is a breaking change
  "boat":   { "id", "name", "sail_number", "design" },
  "sections": {
    "s01_executive":        {…ExecutiveSummaryFacts…},
    "s02_identity":         {…IdentityFacts…},
    "s03_rating_anatomy":   {…RatingAnatomyFacts…},
    "s04_rating_evolution": {…RatingEvolutionFacts…},
    "s05_class_context":    {…ClassContextFacts…},
    "s06_performance":      {…PerformanceFacts…},
    "s07_sensitivity":      {…SensitivityFacts…},
    "s08_optimisation":     {…OptimisationFacts…},
    "s09_formula_drift":    {…FormulaDriftFacts…},
    "s10_rivals":           {…RivalsFacts…},
    "s11_appendix":         {…AppendixFacts…}
  },
  "engines": {
    "rai":              {…RAIResult…},          // analysis.performance.compute_rai
    "design_model":     {…RegressionResult…},   // analysis.regression (per design)
    "fleet_wide_model": {…RegressionResult…},   // Tier-C fleet model
    "smart_boats":      {…}                     // analysis.performance.get_smart_boats
  },
  "facts_sha256": "…"  // hash of canonical(sections+engines)
}
```

Determinism rules: floats rounded to 6dp; `Decimal`→float, dates→ISO
strings; semantically unordered lists (rivals, head-to-head, recent
results, coefficients) re-sorted on explicit keys; JSON serialised with
sorted keys. Given identical table contents the bundle is byte-identical
(there is a test for exactly that).

`validate_report_facts_bundle()` is the shape gate the narrative generator
and CI use to fail fast on malformed bundles.

## 2. Golden fixtures

`irc_data.analysis.backtest` + `api/tests/report/golden/`:

* `dataset.json` — a self-contained extract of the boat's entire report
  universe (its rows + every boat that ever shared a race with it).
* `golden_report_facts_v1.json` — the bundle the current models must
  reproduce, stamped with the fixture's provenance.

The harness seeds a throwaway PostgreSQL database from `dataset.json`
(so the golden gate is hermetic and does not depend on dev-DB state),
rebuilds the bundle with the current code, and diffs **every figure**
against the golden file.

Fixture boats (from the issue's acceptance criteria): **Chilli Pepper**
(Tier-A cert path), **Diablo-J** (13-season history → the held-out-season
RAI workhorse), **Kestrel** (modern era, constant TCC).

### Tolerances (stated)

| What | Tolerance | Where |
| --- | --- | --- |
| Any figure in the bundle | abs 5e-3 / rel 1e-3 | `DEFAULT_ABS_TOL` / `DEFAULT_REL_TOL` |
| RAI hold-one-season-out stability | ≤ 7.5 RAI points | `RAI_STABILITY_TOL` |
| Tier-C holdout skill | MAE ≤ 0.040, R² ≥ 0.80 (seed 42, 80/20) | `RATING_MODEL_HOLDOUT_*` |

## 3. Backtesting

`backtest_rai_held_out_seasons(engine, boat_id)` replays the RAI engine
over the fixture history, hiding one season at a time:

* **stability** — |full-history RAI − RAI-without-season-S| per season S;
  the max gap is gated,
* **predictive value** — Spearman correlation between RAI computed over
  prior seasons and the RAI realised in the held-out season (reported in
  the eval report for review).

`backtest_rating_model_holdout(engine)` refits the Tier-C Ridge pipeline
on a deterministic 80/20 split and reports held-out MAE / R².

## 4. CI regression gate

* pytest marker `model_regression` (see `api/pyproject.toml`) selects the
  whole battery: `pytest tests/report -m model_regression`.
* `.github/workflows/model-regression.yml` runs it on any PR touching
  `analysis/`, `report/`, the fixtures, or the gate itself, against a
  service Postgres. `SM01_REQUIRE_DB=1` turns the local skip into a hard
  failure in CI.
* `api/scripts/run_model_backtest.py` is the CLI equivalent and writes
  `api/test-results/sm_01_08_eval_report.json` — the evaluation report the
  report owner reviews once.

A failing gate means one of: (a) the model regressed → fix it; (b) the
movement is intended → re-snapshot
(`api/scripts/sm_01_08_build_golden.py`) and get the figure diff reviewed
in the PR. Silently moving report numbers is not possible.

## 5. Acceptance criteria → implementation

| Criterion | Where |
| --- | --- |
| Golden fixtures for Chilli Pepper, Diablo-J and Kestrel reproduce every figure in the design reports within stated tolerance | `test_golden_report_facts.py::TestGoldenBundles` |
| Backtest RAI predictive value on held-out seasons | `backtest_rai_held_out_seasons` + `test_rai_backtest.py` |
| CI blocks model changes that regress | `test_model_regression_gate.py` + `model-regression.yml` |
| Emits the ReportFactsV1 bundle AI-01-06 consumes | `facts_bundle.py` (`SCHEMA_VERSION = "ReportFactsV1"`) |
| Eval report for review | `api/test-results/sm_01_08_eval_report.json` |
