# SM-01-08 — Golden fixtures & model backtesting

Model changes are tested like code: the design reports for three fixture
boats must reproduce **every figure** in their `ReportFactsV1` bundles
within the stated tolerance, and the RAI metric must stay stable on
held-out seasons. CI (`.github/workflows/model-regression.yml`) blocks any
change that regresses.

## Layout

```
golden/
├── chilli_pepper/
│   ├── dataset.json                  # self-contained DB extract (boats,
│   │                                 # snapshots, certs, results, identities)
│   └── golden_report_facts_v1.json   # ReportFactsV1 bundle the models must
│                                     # reproduce (sha256-stamped)
├── diablo_j/ …
└── kestrel/ …
```

| Fixture boat   | Why it earns its place                                                        |
| -------------- | ----------------------------------------------------------------------------- |
| CHILLI PEPPER  | Tier-A path: parsed IRC certificate, Sunfast 3300 class, 2020–2024 racing      |
| DIABLO-J       | Long history (2007–2022, 199 races, 13 held-out seasons), J/92 class context   |
| KESTREL        | Modern era (2021–2026), constant TCC, Sunfast 3300 class                        |

DIABLO-J's design class is NULL in the upstream extract; the fixture
restores the `J/92` design context that her design report is generated
against (recorded in `dataset.json` — `design_canonical`).

## Tolerances (stated)

* Golden figures: absolute **5e-3** / relative **1e-3**
  (`DEFAULT_ABS_TOL` / `DEFAULT_REL_TOL` in `irc_data/analysis/backtest.py`)
* RAI held-out-season stability: **≤ 7.5 RAI points**
  (`RAI_STABILITY_TOL`)
* Rating-model holdout: **MAE ≤ 0.040**, **R² ≥ 0.80** on a deterministic
  80/20 split (seed 42)

## Workflows

Run the gate locally (needs Postgres; the tests provision their own
scratch databases):

```bash
cd api
python -m pytest tests/report -m model_regression -q
# or the CI entry point, which also writes the eval report:
python scripts/run_model_backtest.py
```

The evaluation report lands in `api/test-results/sm_01_08_eval_report.json`
— this is the artifact the report owner (Stuart) reviews once.

If a model change **intentionally** moves figures:

```bash
cd api
python scripts/sm_01_08_build_golden.py          # re-snapshot all boats
git diff tests/report/golden                      # review every moved figure
```

To re-extract the underlying data from the dev database (rare — only when
the fixture universe itself should change):

```bash
python scripts/sm_01_08_extract_fixture.py
python scripts/sm_01_08_build_golden.py
```
