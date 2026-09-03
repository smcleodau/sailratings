#!/usr/bin/env python3
"""SM-01-08 — model regression gate (CI entry point).

Runs the full backtesting battery and exits non-zero if anything regresses:

1. Golden fixtures — for Chilli Pepper, Diablo-J and Kestrel the
   ReportFactsV1 bundle is rebuilt from a checked-in dataset inside a
   scratch database and every figure must reproduce the checked-in golden
   bundle within the stated tolerance (``DEFAULT_ABS_TOL`` /
   ``DEFAULT_REL_TOL`` in ``irc_data.analysis.backtest``).
2. RAI held-out-season backtest — per fixture boat, the full-history RAI
   must remain within ``RAI_STABILITY_TOL`` of each hold-one-season-out
   RAI (predictive value / stability of the metric).
3. Rating-model holdout — the fleet-wide Tier-C Ridge model refit on a
   deterministic 80/20 split must clear ``RATING_MODEL_HOLDOUT_MAE_MAX``
   and ``RATING_MODEL_HOLDOUT_R2_MIN``.

Writes the evaluation report (the artifact reviewed by the report owner)
to ``api/test-results/sm_01_08_eval_report.json``.

Usage::

    python api/scripts/run_model_backtest.py [--write-report PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from irc_data.analysis.backtest import (  # noqa: E402
    GOLDEN_BOATS,
    GoldenComparison,
    backtest_rai_held_out_seasons,
    backtest_rating_model_holdout,
    compare_bundles,
    create_scratch_db,
    drop_scratch_db,
    fb_boat_id,
    golden_bundle_path,
    golden_dataset_path,
    load_fixture_dataset,
    RATING_MODEL_HOLDOUT_MAE_MAX,
    RATING_MODEL_HOLDOUT_R2_MIN,
    RAI_STABILITY_TOL,
)
from irc_data.api.services.report.facts_bundle import (  # noqa: E402
    SCHEMA_VERSION,
    build_report_facts,
    validate_report_facts_bundle,
)
from irc_data.config import DATABASE_URL  # noqa: E402

DEFAULT_REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "test-results" / "sm_01_08_eval_report.json"
)


def _admin_url(url: str) -> str:
    if url.rstrip("/").endswith("/postgres"):
        return url
    base, _, _db = url.rpartition("/")
    return f"{base}/postgres"


def run_golden_battery(admin_url: str, database_url: str) -> list[GoldenComparison]:
    """Rebuild every golden bundle in its own scratch DB and diff."""
    from irc_data.analysis.backtest import _iter_figures

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    results: list[GoldenComparison] = []

    for fb in GOLDEN_BOATS:
        golden_path = golden_bundle_path(fb.slug)
        dataset_path = golden_dataset_path(fb.slug)
        if not golden_path.exists() or not dataset_path.exists():
            results.append(GoldenComparison(
                boat_slug=fb.slug,
                passed=False,
                figures_checked=0,
                violations=[],
            ))
            print(f"[golden] {fb.slug}: MISSING artifacts — run sm_01_08_extract_fixture.py "
                  f"and sm_01_08_build_golden.py", flush=True)
            continue

        golden = json.loads(golden_path.read_text())
        dataset = json.loads(dataset_path.read_text())

        db = create_scratch_db(admin, f"sm0108_gate_{fb.slug}")
        try:
            eng = create_engine(database_url.rpartition("/")[0] + f"/{db}")
            load_fixture_dataset(eng, dataset)
            actual = build_report_facts(eng, fb_boat_id(eng, fb))
            eng.dispose()
        finally:
            drop_scratch_db(admin, db)

        # The 'fixture' block is provenance stamped by the generator, not a
        # figure — strip it before the diff.
        golden.pop("fixture", None)

        diffs = compare_bundles(golden, actual)
        n_figures = sum(
            1 for p, _ in _iter_figures(golden) if not p.endswith("facts_sha256")
        )
        results.append(GoldenComparison(
            boat_slug=fb.slug,
            passed=not diffs,
            figures_checked=n_figures,
            violations=diffs,
        ))
        status = "PASS" if not diffs else f"FAIL ({len(diffs)} figures moved)"
        print(f"[golden] {fb.slug}: {status} ({n_figures} figures checked)", flush=True)
        for d in diffs[:10]:
            print(f"    {d.path}: golden={d.golden!r} actual={d.actual!r}", flush=True)

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=DATABASE_URL)
    ap.add_argument("--write-report", default=str(DEFAULT_REPORT_PATH))
    ap.add_argument(
        "--skip-golden",
        action="store_true",
        help="skip the golden-fixture battery (run backtests only)",
    )
    args = ap.parse_args()

    engine = create_engine(args.database_url)
    failures: list[str] = []

    # ── 1. Golden fixtures ────────────────────────────────────────────
    golden_results: list[GoldenComparison] = []
    if not args.skip_golden:
        golden_results = run_golden_battery(_admin_url(args.database_url), args.database_url)
        for g in golden_results:
            if not g.passed:
                failures.append(
                    f"golden fixture {g.boat_slug}: {len(g.violations)} figures moved "
                    f"(model change must either be reverted or the fixture re-snapshotted "
                    f"with review)"
                )

    # ── 2. RAI held-out seasons (run on the live DB where history lives) ──
    rai_reports: dict[str, dict] = {}
    fixture_ids = {"chilli_pepper": 12067, "diablo_j": 792, "kestrel": 21068}
    for slug, boat_id in fixture_ids.items():
        try:
            rb = backtest_rai_held_out_seasons(engine, boat_id)
        except Exception as e:  # pragma: no cover - defensive
            rb = {"error": str(e)}
        rai_reports[slug] = rb
        if "error" in rb:
            print(f"[rai-backtest] {slug}: ERROR {rb['error']}", flush=True)
            failures.append(f"RAI backtest {slug}: {rb['error']}")
            continue
        gap = rb.get("max_stability_gap")
        ok = gap is not None and gap <= RAI_STABILITY_TOL
        print(
            f"[rai-backtest] {slug}: full={rb.get('rai_full_history')} "
            f"seasons={rb.get('n_seasons_tested')} max_gap={gap} "
            f"predictive_spearman={rb.get('predictive_spearman')} "
            f"{'PASS' if ok else 'FAIL'}",
            flush=True,
        )
        if not ok:
            failures.append(
                f"RAI backtest {slug}: max stability gap {gap} exceeds "
                f"tolerance {RAI_STABILITY_TOL}"
            )

    # ── 3. Rating-model holdout ───────────────────────────────────────
    holdout = {}
    try:
        holdout = backtest_rating_model_holdout(engine)
        mae, r2 = holdout.get("holdout_mae"), holdout.get("holdout_r2")
        mae_ok = mae is not None and mae <= RATING_MODEL_HOLDOUT_MAE_MAX
        r2_ok = r2 is not None and r2 >= RATING_MODEL_HOLDOUT_R2_MIN
        print(
            f"[rating-model] holdout MAE={mae} (max {RATING_MODEL_HOLDOUT_MAE_MAX}) "
            f"R2={r2} (min {RATING_MODEL_HOLDOUT_R2_MIN}) "
            f"{'PASS' if (mae_ok and r2_ok) else 'FAIL'}",
            flush=True,
        )
        if not mae_ok:
            failures.append(f"rating-model holdout MAE {mae} exceeds {RATING_MODEL_HOLDOUT_MAE_MAX}")
        if not r2_ok:
            failures.append(f"rating-model holdout R2 {r2} below {RATING_MODEL_HOLDOUT_R2_MIN}")
    except Exception as e:  # pragma: no cover - defensive
        holdout = {"error": str(e)}
        failures.append(f"rating-model holdout errored: {e}")

    # ── Evaluation report artifact ────────────────────────────────────
    report = {
        "eval_version": SCHEMA_VERSION,
        "golden_fixtures": [g.to_dict() for g in golden_results],
        "rai_backtests": rai_reports,
        "rating_model_holdout": holdout,
        "thresholds": {
            "golden_abs_tol": 5e-3,
            "golden_rel_tol": 1e-3,
            "rai_stability_tol": RAI_STABILITY_TOL,
            "rating_model_holdout_mae_max": RATING_MODEL_HOLDOUT_MAE_MAX,
            "rating_model_holdout_r2_min": RATING_MODEL_HOLDOUT_R2_MIN,
        },
        "failures": failures,
        "passed": not failures,
    }
    out = Path(args.write_report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"[report] wrote {out}", flush=True)

    if failures:
        print("\nSM-01-08 GATE: FAIL", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    print("\nSM-01-08 GATE: PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
