#!/usr/bin/env python3
"""SM-01-08 — (re)generate golden ReportFactsV1 bundles.

For each registered fixture boat this seeds a scratch database from
``tests/report/golden/<slug>/dataset.json``, rebuilds the ReportFactsV1
bundle with the current model code, and writes
``tests/report/golden/<slug>/golden_report_facts_v1.json``.

Run this intentionally when the model or builders change in a way that is
*meant* to move report figures; the resulting diff is reviewed in the PR.
CI (``run_model_backtest.py``) treats the checked-in file as immutable.

Usage::

    python api/scripts/sm_01_08_build_golden.py [--only kestrel]
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
    create_scratch_db,
    drop_scratch_db,
    fb_boat_id,
    golden_bundle_path,
    golden_dataset_path,
    load_fixture_dataset,
)
from irc_data.api.services.report.facts_bundle import (  # noqa: E402
    build_report_facts,
    bundle_to_json,
    validate_report_facts_bundle,
)
from irc_data.config import DATABASE_URL  # noqa: E402


def _admin_url(url: str) -> str:
    # Point at the server's maintenance DB for CREATE/DROP DATABASE.
    if url.rstrip("/").endswith("/postgres"):
        return url
    base, _, _db = url.rpartition("/")
    return f"{base}/postgres"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=[b.slug for b in GOLDEN_BOATS], default=None)
    ap.add_argument("--database-url", default=DATABASE_URL)
    args = ap.parse_args()

    admin = create_engine(_admin_url(args.database_url), isolation_level="AUTOCOMMIT")

    for fb in GOLDEN_BOATS:
        if args.only and fb.slug != args.only:
            continue
        ds_path = golden_dataset_path(fb.slug)
        if not ds_path.exists():
            raise SystemExit(f"missing dataset {ds_path} — run sm_01_08_extract_fixture.py first")
        dataset = json.loads(ds_path.read_text())

        db = create_scratch_db(admin, f"sm0108_golden_{fb.slug}")
        try:
            eng = create_engine(args.database_url.rpartition("/")[0] + f"/{db}")
            load_fixture_dataset(eng, dataset)
            boat_id = fb_boat_id(eng, fb)
            bundle = build_report_facts(eng, boat_id)
            eng.dispose()
        finally:
            drop_scratch_db(admin, db)

        violations = validate_report_facts_bundle(bundle)
        if violations:
            raise SystemExit(f"{fb.slug}: bundle failed validation: {violations}")

        # Stamp fixture metadata onto the golden artifact.
        bundle["fixture"] = {
            "slug": fb.slug,
            "boat_name": fb.boat_name,
            "sail_number": fb.sail_number,
            "design": fb.design,
            "description": fb.description,
        }
        out = golden_bundle_path(fb.slug)
        out.write_text(bundle_to_json(bundle) + "\n")
        s01 = bundle["sections"]["s01_executive"]
        print(
            f"{fb.slug}: boat_id={boat_id} tcc={s01.get('tcc_now')} "
            f"finishes={s01.get('finishes')} sha256={bundle['facts_sha256'][:16]}… -> {out}"
        )


if __name__ == "__main__":
    main()
