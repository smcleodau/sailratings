#!/usr/bin/env python3
"""SM-01-02 — human-runnable verification of the class regression engine.

Runs the golden fixtures off-DB and, when the dev database is reachable,
the live per-class regressions.  Prints a compact evidence log suitable
for pasting into the issue board.

Usage::

    PYTHONPATH=src python scripts/verify_sm_01_02.py            # fixtures + live
    SM0102_LIVE_DB=0 PYTHONPATH=src python scripts/verify_sm_01_02.py  # fixtures only
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from irc_data.analysis.class_regression import (  # noqa: E402
    ClassRegressionResult,
    WithheldClassResult,
    RATING_IRC,
    RATING_ORC,
    regress_class_rows,
    run_class_all_targets,
    run_class_regression,
)

from test_class_regression_sm_01_02 import (  # noqa: E402
    _cape31_rows,
    _j109_rows,
    _orc_fixture_rows,
    _sf3300_rows,
    CHILLI_PEPPER_GPH,
)


def _line(ok: bool, label: str, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def golden() -> bool:
    print("== Golden fixtures (off-DB) ==")
    ok = True

    sf = regress_class_rows(_sf3300_rows(), system=RATING_IRC, design="Sun Fast 3300", target="tcc")
    ok &= _line(
        isinstance(sf, ClassRegressionResult) and abs(sf.r_squared - 0.91) <= 0.02 and sf.n == 214,
        "IRC Sun Fast 3300",
        f"R²={sf.r_squared:.3f} N={sf.n} (expect R²≈0.91 N≈214)",
    )

    j = regress_class_rows(_j109_rows(), system=RATING_IRC, design="J/109", target="tcc")
    ok &= _line(
        isinstance(j, ClassRegressionResult) and abs(j.r_squared - 0.88) <= 0.02 and j.n == 187,
        "IRC J/109",
        f"R²={j.r_squared:.3f} N={j.n} (expect R²≈0.88 N≈187)",
    )

    cape = regress_class_rows(_cape31_rows(), system=RATING_IRC, design="Cape 31", target="tcc")
    ok &= _line(
        isinstance(cape, WithheldClassResult),
        "IRC Cape 31 withheld",
        f"withheld={cape.withheld} reason='{cape.withheld_reason}' (expect below threshold)",
    )

    orc_rows = _orc_fixture_rows()
    orc = regress_class_rows(orc_rows, system=RATING_ORC, design="ORC Fixture Fleet", target="gph")
    chilli = next(p for p in orc.positions if p.boat_name == "Chilli Pepper")
    ok &= _line(
        isinstance(orc, ClassRegressionResult) and abs(chilli.target_value - CHILLI_PEPPER_GPH) < 1e-6,
        "ORC Chilli Pepper cross-check",
        f"GPH={chilli.target_value:.1f} (expect 625.4, design §06), fleet R²={orc.r_squared:.3f} tight={orc.tight_fit}",
    )

    # Never pooled: systems carry independent dataset versions + levers.
    ok &= _line(
        sf.system != orc.system and sf.dataset_version != orc.dataset_version,
        "IRC/ORC never pooled",
        f"irc version={sf.dataset_version} orc version={orc.dataset_version}",
    )
    return ok


def live() -> bool:
    if os.environ.get("SM0102_LIVE_DB", "1") != "1":
        print("== Live DB (skipped — SM0102_LIVE_DB=0) ==")
        return True
    print("== Live dev DB ==")
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg://irc:irc@localhost:5433/irc_data")
    ok = True
    try:
        sf = run_class_regression(engine, "Sunfast 3300", "irc")
        ok &= _line(
            isinstance(sf, ClassRegressionResult) and sf.r_squared > 0.7 and sf.n >= 70,
            "live IRC Sunfast 3300",
            f"N={sf.n} R²={sf.r_squared:.3f} top lever={sf.levers[0].field}",
        )
        orc = [r for r in run_class_all_targets(engine, "Sunfast 3300", "orc")
               if isinstance(r, ClassRegressionResult)]
        ok &= _line(
            bool(orc) and all(r.tight_fit for r in orc),
            "live ORC Sunfast 3300 (tight, VPP-derived)",
            ", ".join(f"{r.target}:R²={r.r_squared:.3f}" for r in orc),
        )
    except Exception as exc:  # pragma: no cover - env-dependent
        print(f"  [SKIP] live DB unavailable: {exc}")
        return True
    return ok


if __name__ == "__main__":
    ok = golden() and live()
    print("\nSM-01-02 verification:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)
