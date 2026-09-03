"""OPS-02-12 — IRC history reconstruction at scale.

Runs the Wayback TCC harvest and the ``irc_backfill`` orchestrator at
scale, in the priority order the issue specifies, with progress recorded
in ``admin_metrics`` throughout so the run is observable from the
database itself.

Pipeline phases (each optional, all idempotent / resumable):

  A. ``wayback-tcc`` harvest   — pull archived public IRC TCC listings
     (ClubListing / tcc-listing CSVs) from the Wayback CDX index into
     ``TCC_LISTINGS_DIR/historical``.  Public listings only — the CDX
     patterns target ircrating.org's public upload dirs, no paywalled
     certs.

  B. history import            — load every harvested snapshot into
     ``tcc_snapshots`` (mid-year anchored, match-first; see
     :mod:`irc_data.scrapers.tcc_history_loader`).  This is the phase
     that actually moves the acceptance KPI.

  C. ``irc_backfill``          — probe live ``ircrating.org/pdfdirectory``
     then Wayback for the historical certificate *PDFs*, in priority
     order: (1) boats with race results, (2) GBR/AUS/IRL fleet,
     (3) everything else.  State persists to
     ``.irc_backfill_state.json`` so the run is resumable; a progress
     row is written to ``admin_metrics`` every ``--progress-every``
     probes.

  D. KPI verification          — compute the acceptance KPI
     (:mod:`irc_data.db.history_kpi`) and record it in ``admin_metrics``
     (phase=before/after around the whole run).

Priority queue (Phase C)
------------------------
Boats are ordered:

  1. ``raced``   — boats appearing in ``race_results`` (most recent first).
  2. ``fleet``   — remaining boats in the GBR / AUS / IRL fleets
                   (``boats.country`` in that set, or a sail-number prefix
                   match when country is unpopulated).
  3. ``rest``    — everything else.

Only the ``--limit``-cap is applied after ordering, so a capped run
always works on the highest-priority boats first.

Usage::

    python3 -m scripts.ops_02_12_history_reconstruction [--dry-run]
        [--skip-harvest] [--skip-import] [--skip-backfill]
        [--backfill-limit N] [--progress-every N]
        [--start-year YYYY] [--end-year YYYY] [--max-per-pattern N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.config import TCC_LISTINGS_DIR
from irc_data.db.connection import get_engine
from irc_data.db.history_kpi import compute_tcc_history_kpi

# ---------------------------------------------------------------------------
# Metric names (admin_metrics)
# ---------------------------------------------------------------------------
METRIC_HARVEST = "irc_history.wayback_harvest"
METRIC_IMPORT = "irc_history.tcc_import"
METRIC_BACKFILL = "irc_history.cert_backfill"
METRIC_BACKFILL_PROGRESS = "irc_history.cert_backfill.progress"
METRIC_KPI = "irc_history.tcc_history_coverage"
METRIC_RUN = "irc_history.run"

PRIORITY_FLEET = ("GBR", "AUS", "IRL")


# ---------------------------------------------------------------------------
# admin_metrics helper (mirrors the OPS-02-09 recorder)
# ---------------------------------------------------------------------------


def record_metric(
    conn,
    metric: str,
    *,
    scope: str = "",
    phase: str = "",
    value_num: float | None = None,
    value_text: str | None = None,
    meta: dict | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO admin_metrics
                (metric, scope, phase, value_num, value_text, meta)
            VALUES
                (:metric, :scope, :phase, :value_num, :value_text,
                 CAST(:meta AS jsonb))
            """
        ),
        {
            "metric": metric,
            "scope": scope,
            "phase": phase,
            "value_num": value_num,
            "value_text": value_text,
            "meta": json.dumps(meta or {}),
        },
    )


def record_kpi(engine: Engine, phase: str) -> dict:
    kpi = compute_tcc_history_kpi(engine)
    with engine.begin() as conn:
        record_metric(
            conn,
            METRIC_KPI,
            phase=phase,
            value_num=kpi["pct_span"],
            value_text="meets_acceptance" if kpi["meets_acceptance"] else "below_acceptance",
            meta=kpi,
        )
    return kpi


# ---------------------------------------------------------------------------
# Phase C: prioritized cert-index for irc_backfill
# ---------------------------------------------------------------------------


def build_prioritized_index(engine: Engine, tcc_dir: Path) -> list[dict]:
    """Order the harvested cert index by the OPS-02-12 priority queue.

    Reads the master cert index from the harvested CSVs, then annotates
    each entry with a priority tier from the live DB (race results +
    fleet country).  Returns the index sorted tier → recency.
    """
    from irc_data.scrapers.cert_index import build_index_from_tcc_dir

    index = build_index_from_tcc_dir(Path(tcc_dir))
    if not index:
        return []

    with engine.connect() as conn:
        raced = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    """
                    SELECT b.cert_number, MAX(rr.event_date) AS last_race
                      FROM race_results rr
                      JOIN boats b ON b.id = rr.boat_id
                     WHERE b.cert_number IS NOT NULL AND b.cert_number <> ''
                     GROUP BY b.cert_number
                    """
                )
            ).fetchall()
        }
        fleet = {
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT DISTINCT cert_number FROM boats
                     WHERE cert_number IS NOT NULL AND cert_number <> ''
                       AND (country = ANY(:fleet)
                            OR UPPER(sail_number) ~ '^(GBR|AUS|IRL)')
                    """
                ),
                {"fleet": list(PRIORITY_FLEET)},
            ).fetchall()
        }

    def tier(entry: dict) -> int:
        cert = (entry.get("cert_number") or "").strip()
        if cert in raced:
            return 0
        if cert in fleet:
            return 1
        return 2

    def sort_key(entry: dict):
        t = tier(entry)
        # Within 'raced', most-recent racers first (NULLS last).
        last = raced.get((entry.get("cert_number") or "").strip())
        recency = -(last.toordinal() if isinstance(last, date) else 0)
        return (t, recency)

    ordered = sorted(index, key=sort_key)
    counts = {"raced": 0, "fleet": 0, "rest": 0}
    for e in ordered:
        counts[{0: "raced", 1: "fleet", 2: "rest"}[tier(e)]] += 1
    return {"index": ordered, "tier_counts": counts}


# ---------------------------------------------------------------------------
# Phase C runner: irc_backfill with live progress
# ---------------------------------------------------------------------------


async def run_backfill(
    engine: Engine,
    index: list[dict],
    *,
    resume: bool = True,
    progress_every: int = 100,
    dry_run: bool = False,
) -> dict:
    """Drive :func:`irc_data.scrapers.irc_backfill.probe_cert` over the
    prioritized index, persisting backfill state and emitting an
    ``admin_metrics`` progress row every ``progress_every`` probes.
    """
    from irc_data.scrapers import irc_backfill as bf

    state = bf._load_state() if resume else {"done": []}
    done: set[str] = set(state.get("done", []))
    stats = {"found_live": 0, "found_wayback": 0, "not_found": 0, "probed": 0}

    for entry in index:
        cert_no = (entry.get("cert_number") or "").strip()
        if not cert_no or cert_no in done:
            continue
        if dry_run:
            stats["probed"] += 1
            continue
        try:
            result = await bf.probe_cert(
                cert_number=cert_no,
                boat_name=entry.get("boat_name", ""),
                sail_number=entry.get("sail_number", ""),
                year=entry.get("year"),
            )
        except Exception as exc:  # noqa: BLE001 - keep the sweep going
            print(f"  probe_cert({cert_no}) raised {exc}; treating as not_found")
            result = {"source": None, "status": "not_found"}
        if result["status"] == "found":
            stats[f"found_{result['source']}"] += 1
        else:
            stats["not_found"] += 1
        stats["probed"] += 1
        done.add(cert_no)
        state["done"] = sorted(done)
        bf._save_state(state)

        if progress_every and stats["probed"] % progress_every == 0:
            with engine.begin() as conn:
                record_metric(
                    conn,
                    METRIC_BACKFILL_PROGRESS,
                    phase="run",
                    value_num=float(stats["probed"]),
                    meta={**stats, "queue_total": len(index)},
                )
            print(
                f"  progress: {stats['probed']}/{len(index)} probed "
                f"(live={stats['found_live']}, wayback={stats['found_wayback']})"
            )

    return stats


# ---------------------------------------------------------------------------
# Phases A & B
# ---------------------------------------------------------------------------


async def run_harvest(
    start_year: int,
    end_year: int,
    out_dir: Path,
    max_per_pattern: int | None,
) -> list[dict]:
    from irc_data.scrapers.wayback import harvest_tcc_archives

    return await harvest_tcc_archives(
        start_year=start_year,
        end_year=end_year,
        out_dir=out_dir,
        max_per_pattern=max_per_pattern,
    )


def run_import(engine: Engine, tcc_dir: Path) -> dict:
    from irc_data.scrapers.tcc_history_loader import import_historical_tcc_dir

    return import_historical_tcc_dir(engine, tcc_dir, progress_every=5)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(engine: Engine, args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"dry_run": args.dry_run}
    tcc_dir = Path(args.tcc_dir) if args.tcc_dir else (TCC_LISTINGS_DIR / "historical")

    # KPI before
    kpi_before = record_kpi(engine, "before")
    report["kpi_before"] = kpi_before

    # Phase A — wayback harvest (public listings only)
    if not args.skip_harvest and not args.dry_run:
        archives = asyncio.run(
            run_harvest(args.start_year, args.end_year, tcc_dir, args.max_per_pattern)
        )
        by_year: dict[int, int] = {}
        for a in archives:
            by_year[a["year"]] = by_year.get(a["year"], 0) + 1
        report["harvest"] = {"downloaded": len(archives), "by_year": by_year}
        with engine.begin() as conn:
            record_metric(
                conn,
                METRIC_HARVEST,
                scope=str(tcc_dir),
                phase="run",
                value_num=float(len(archives)),
                meta={"by_year": by_year, "start_year": args.start_year,
                      "end_year": args.end_year},
            )
    else:
        report["harvest"] = {"skipped": True}

    # Phase B — import harvested snapshots into tcc_snapshots
    if not args.skip_import:
        if args.dry_run:
            report["import"] = {"skipped": "dry-run"}
        else:
            imp = run_import(engine, tcc_dir)
            report["import"] = imp
            with engine.begin() as conn:
                record_metric(
                    conn,
                    METRIC_IMPORT,
                    scope=str(tcc_dir),
                    phase="run",
                    value_num=float(imp["snapshots_written"]),
                    meta=imp,
                )
    else:
        report["import"] = {"skipped": True}

    # Phase C — prioritized irc_backfill
    if not args.skip_backfill:
        built = build_prioritized_index(engine, tcc_dir)
        if built:
            index, tiers = built["index"], built["tier_counts"]
            if args.backfill_limit:
                index = index[: args.backfill_limit]
            report["backfill_queue"] = {"total": len(index), **tiers}
            bf_stats = asyncio.run(
                run_backfill(
                    engine,
                    index,
                    resume=not args.no_resume,
                    progress_every=args.progress_every,
                    dry_run=args.dry_run,
                )
            )
            report["backfill"] = bf_stats
            if not args.dry_run:
                with engine.begin() as conn:
                    record_metric(
                        conn,
                        METRIC_BACKFILL,
                        phase="run",
                        value_num=float(bf_stats["probed"]),
                        value_text="dry_run" if args.dry_run else "applied",
                        meta={**bf_stats, **tiers},
                    )
        else:
            report["backfill"] = {"skipped": "empty index (run harvest first)"}
    else:
        report["backfill"] = {"skipped": True}

    # KPI after + run summary
    kpi_after = record_kpi(engine, "after")
    report["kpi_after"] = kpi_after
    with engine.begin() as conn:
        record_metric(
            conn,
            METRIC_RUN,
            phase="run",
            value_num=kpi_after["pct_span"],
            value_text="meets_acceptance" if kpi_after["meets_acceptance"] else "below_acceptance",
            meta={
                "kpi_before": kpi_before,
                "kpi_after": kpi_after,
                "phases": {
                    "harvest": not args.skip_harvest and not args.dry_run,
                    "import": not args.skip_import and not args.dry_run,
                    "backfill": not args.skip_backfill,
                },
                "dry_run": args.dry_run,
            },
        )
    return report


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def _print(report: dict[str, Any]) -> None:
    tag = "[DRY RUN] " if report.get("dry_run") else ""
    print()
    print("=" * 74)
    print(f"{tag}OPS-02-12 IRC history reconstruction — run report")
    print("=" * 74)

    kb, ka = report["kpi_before"], report["kpi_after"]
    print(
        "  KPI  (racers 24m with >=3y TCC history):  "
        f"{_pct(kb['pct_span'])} ({kb['with_3y_span']}/{kb['racers']})  ->  "
        f"{_pct(ka['pct_span'])} ({ka['with_3y_span']}/{ka['racers']})   "
        f"[acceptance >= 60%: {'MET' if ka['meets_acceptance'] else 'NOT MET'}]"
    )
    print(
        f"       stricter distinct-years measure:   {_pct(kb['pct_distinct'])}"
        f"  ->  {_pct(ka['pct_distinct'])}"
    )

    h = report.get("harvest", {})
    if h.get("skipped"):
        print("  harvest: skipped")
    else:
        print(
            f"  harvest: {h.get('downloaded', 0)} snapshot(s) across "
            f"{len(h.get('by_year', {}))} year(s)"
        )

    i = report.get("import", {})
    if i.get("skipped"):
        print(f"  import: skipped ({i['skipped']})")
    else:
        print(
            f"  import: {i.get('snapshots_written', 0)} snapshots from "
            f"{i.get('files', 0)} file(s); {i.get('matched_boats', 0)} boats matched, "
            f"{i.get('coverage_boats_3y', 0)} boats with >=3y coverage"
        )

    bq = report.get("backfill_queue")
    if bq:
        print(
            f"  backfill queue: {bq['total']} certs "
            f"(raced={bq['raced']}, fleet={bq['fleet']}, rest={bq['rest']})"
        )
    b = report.get("backfill", {})
    if b.get("skipped"):
        print(f"  backfill: skipped ({b['skipped']})")
    elif b:
        print(
            f"  backfill: probed={b.get('probed', 0)} "
            f"live={b.get('found_live', 0)} wayback={b.get('found_wayback', 0)} "
            f"missing={b.get('not_found', 0)}"
        )
    print()


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; no DB writes / downloads")
    ap.add_argument("--skip-harvest", action="store_true", help="skip Phase A (wayback harvest)")
    ap.add_argument("--skip-import", action="store_true", help="skip Phase B (tcc_snapshots import)")
    ap.add_argument("--skip-backfill", action="store_true", help="skip Phase C (cert PDF backfill)")
    ap.add_argument("--backfill-limit", type=int, default=None, help="cap Phase C probes")
    ap.add_argument("--progress-every", type=int, default=100, help="admin_metrics progress cadence")
    ap.add_argument("--no-resume", action="store_true", help="ignore .irc_backfill_state.json")
    ap.add_argument("--start-year", type=int, default=2010)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--max-per-pattern", type=int, default=None, help="smoke-test cap per CDX pattern")
    ap.add_argument("--tcc-dir", default=None, help="override harvested-CSV dir")
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()
    engine = get_engine()
    report = run(engine, args)
    _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
