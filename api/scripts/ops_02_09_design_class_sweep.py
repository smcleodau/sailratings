"""OPS-02-09 — finish the design_classes merge/null/backfill sweep and record
the boats.design_canonical NULL rate (before/after) in ``admin_metrics``.

This runner composes the three existing, individually-audited sweeps:

  1. ``scripts.merge_design_classes``       — collapse duplicate canonicals
                                              (audit: ``design_class_merges``)
  2. ``scripts.sweep_boats_design_canonical`` — re-normalize every
                                              ``boats.design_canonical``
                                              (audit: ``boats_design_canonical_sweep``)
  3. ``scripts.null_multi_designer_classes`` — NULL misleading designer/builder
                                              attributions on open classes
                                              (audit: ``design_class_attr_nulls``)

Around them it records the acceptance metrics into ``admin_metrics`` (created
by alembic revision ``0029``):

  * ``boats.design_canonical.null_rate``  phase=before / phase=after
  * ``boats.design_canonical.orphans``    phase=before / phase=after
    (orphan = non-NULL value with no matching ``design_classes.name_canonical``)
  * ``design_classes.null_rate``          phase=after, scope per attribute column
  * ``design_classes.sweep``              phase=run, one row per step outcome
  * ``boats.design_canonical.fk_validation`` — validated / already_valid /
    skipped_orphans, straight from ``pg_constraint.convalidated``

The FK itself (``fk_boats_design_canonical``) is created/validated by
revision ``0029``; this script re-checks its state and records it, so the
metric stream always carries the latest known constraint status.

Idempotent: each sub-sweep is a no-op once converged; ``admin_metrics`` rows
are append-only evidence (one row per run per metric), so re-running adds a
fresh evidence point rather than corrupting prior runs.

Usage:
    python3 -m scripts.ops_02_09_design_class_sweep [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine

METRIC_NULL_RATE = "boats.design_canonical.null_rate"
METRIC_ORPHANS = "boats.design_canonical.orphans"
METRIC_DC_NULL_RATE = "design_classes.null_rate"
METRIC_SWEEP = "design_classes.sweep"
METRIC_FK = "boats.design_canonical.fk_validation"

FK_NAME = "fk_boats_design_canonical"

_DESIGN_ATTR_COLS = (
    "designer",
    "builder",
    "year_first",
    "nominal_loa",
    "nominal_beam",
    "nominal_draft",
    "nominal_displacement",
)


# ---------------------------------------------------------------------------
# Metric capture
# ---------------------------------------------------------------------------


def boats_design_stats(conn) -> dict[str, Any]:
    """NULL rate + orphan counts for boats.design_canonical."""
    row = conn.execute(
        text(
            """
            SELECT
              COUNT(*)                                          AS total,
              COUNT(*) - COUNT(design_canonical)                AS nulls
            FROM boats
            """
        )
    ).one()
    total = int(row.total)
    nulls = int(row.nulls)

    orphan_row = conn.execute(
        text(
            """
            SELECT COUNT(*), COUNT(DISTINCT b.design_canonical)
            FROM boats b
            WHERE b.design_canonical IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM design_classes dc
                  WHERE dc.name_canonical = b.design_canonical
              )
            """
        )
    ).one()

    return {
        "total": total,
        "nulls": nulls,
        "null_rate": (nulls / total) if total else 0.0,
        "orphan_boats": int(orphan_row[0]),
        "orphan_distinct": int(orphan_row[1]),
    }


def design_class_fill(conn) -> dict[str, dict[str, Any]]:
    """Per-column NULL rates on design_classes."""
    cols = ", ".join(f"COUNT(*) - COUNT({c}) AS {c}_nulls" for c in _DESIGN_ATTR_COLS)
    row = conn.execute(
        text(f"SELECT COUNT(*) AS total, {cols} FROM design_classes")
    ).one()
    total = int(row.total)
    out: dict[str, dict[str, Any]] = {}
    for c in _DESIGN_ATTR_COLS:
        nulls = int(getattr(row, f"{c}_nulls"))
        out[c] = {
            "total": total,
            "nulls": nulls,
            "null_rate": (nulls / total) if total else 0.0,
        }
    return out


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


def record_boats_stats(conn, phase: str, stats: dict[str, Any]) -> None:
    record_metric(
        conn,
        METRIC_NULL_RATE,
        phase=phase,
        value_num=stats["null_rate"],
        meta={
            "total_boats": stats["total"],
            "null_boats": stats["nulls"],
        },
    )
    record_metric(
        conn,
        METRIC_ORPHANS,
        phase=phase,
        value_num=float(stats["orphan_boats"]),
        meta={"orphan_distinct_designs": stats["orphan_distinct"]},
    )


# ---------------------------------------------------------------------------
# Sub-sweep runners (import lazily so --dry-run plumbing stays local)
# ---------------------------------------------------------------------------


def _run_merge(engine: Engine, dry_run: bool) -> dict[str, Any]:
    from scripts import merge_design_classes as m

    m.ensure_audit_table(engine)
    rows = m.load_rows(engine)
    boats_by_name = m.boats_count_by_name(engine)
    plan = m.build_plan(rows, boats_by_name)
    clusters, deleted, repointed = m.execute_plan(engine, plan, dry_run)
    return {
        "clusters_planned": len(plan),
        "clusters_merged": clusters,
        "rows_deleted": deleted,
        "boats_repointed": repointed,
    }


def _run_boat_sweep(engine: Engine, dry_run: bool) -> dict[str, Any]:
    from scripts import sweep_boats_design_canonical as s

    return s.sweep(engine, dry_run=dry_run)


def _run_null_sweep(engine: Engine, dry_run: bool) -> dict[str, Any]:
    """NULL multi-designer classes; skipped cleanly if its input JSON is absent."""
    from scripts import null_multi_designer_classes as n

    if not n.AMBIGUOUS_PATH.exists():
        return {
            "status": "skipped",
            "reason": f"{n.AMBIGUOUS_PATH} not found (no ambiguity report)",
        }

    ambiguous = json.loads(n.AMBIGUOUS_PATH.read_text())
    curated_lookup = {n._norm_lookup(c): c for c in n.CURATED_OPEN_CLASSES}

    candidates = []
    for d in ambiguous:
        name = d.get("name") or ""
        des_counts = n._collapse(d.get("designers", []), n.normalize_designer)
        bld_counts = n._collapse(d.get("builders", []), n.normalize_builder)
        if n._norm_lookup(name) not in curated_lookup:
            continue
        null_des = len(des_counts) >= n.MIN_DISTINCT_POST_NORM
        null_bld = len(bld_counts) >= n.MIN_DISTINCT_POST_NORM
        if not (null_des or null_bld):
            continue
        candidates.append(
            {
                "name": name,
                "null_designer": null_des,
                "null_builder": null_bld,
            }
        )

    applied = {"designer": 0, "builder": 0}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS design_class_attr_nulls (
                    id          bigserial PRIMARY KEY,
                    nulled_at   timestamptz NOT NULL DEFAULT now(),
                    design_id   integer NOT NULL,
                    canonical   text NOT NULL,
                    column_name text NOT NULL,
                    old_value   text NOT NULL
                )
                """
            )
        )
        for c in candidates:
            row = conn.execute(
                text(
                    """
                    SELECT id, name_canonical, designer, builder
                    FROM design_classes
                    WHERE LOWER(name_canonical) = LOWER(:n)
                    LIMIT 1
                    """
                ),
                {"n": c["name"]},
            ).fetchone()
            if row is None:
                continue
            for col, flag in (
                ("designer", c["null_designer"]),
                ("builder", c["null_builder"]),
            ):
                old = getattr(row, col)
                if not flag or old is None:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO design_class_attr_nulls
                            (design_id, canonical, column_name, old_value)
                        VALUES (:id, :canon, :col, :old)
                        """
                    ),
                    {"id": row.id, "canon": row.name_canonical, "col": col, "old": old},
                )
                if not dry_run:
                    conn.execute(
                        text(f"UPDATE design_classes SET {col} = NULL WHERE id = :id"),
                        {"id": row.id},
                    )
                applied[col] += 1

    return {"status": "ran", "candidates": len(candidates), "nulled": applied}


# ---------------------------------------------------------------------------
# FK state
# ---------------------------------------------------------------------------


def fk_state(conn) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT convalidated, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = :name AND conrelid = 'boats'::regclass
            """
        ),
        {"name": FK_NAME},
    ).fetchone()
    if row is None:
        return {"exists": False, "validated": False, "definition": None}
    return {
        "exists": True,
        "validated": bool(row[0]),
        "definition": row[1],
    }


def ensure_fk(conn, dry_run: bool) -> dict[str, Any]:
    """Ensure the FK exists and is validated (mirrors revision 0029)."""
    state = fk_state(conn)
    if not state["exists"]:
        if not dry_run:
            conn.execute(
                text(
                    f"""
                    ALTER TABLE boats
                    ADD CONSTRAINT {FK_NAME}
                    FOREIGN KEY (design_canonical)
                    REFERENCES design_classes(name_canonical)
                    ON UPDATE CASCADE
                    ON DELETE SET NULL
                    NOT VALID
                    """
                )
            )
        state = {**state, "recreated": True}

    orphans = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM boats b
            WHERE b.design_canonical IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM design_classes dc
                  WHERE dc.name_canonical = b.design_canonical
              )
            """
        )
    ).scalar() or 0

    outcome = "skipped_orphans"
    if fk_state(conn)["validated"]:
        outcome = "already_valid"
    elif int(orphans) == 0:
        if not dry_run:
            conn.execute(
                text(f"ALTER TABLE boats VALIDATE CONSTRAINT {FK_NAME}")
            )
        outcome = "validated"
    return {"outcome": outcome, "orphans": int(orphans)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(engine: Engine, dry_run: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {"dry_run": dry_run}

    with engine.begin() as conn:
        before = boats_design_stats(conn)
        report["before"] = before
        record_boats_stats(conn, "before", before)

    # --- 1. merge duplicate canonicals ------------------------------------
    report["merge"] = _run_merge(engine, dry_run)
    # --- 2. re-normalize boats.design_canonical ---------------------------
    report["boat_sweep"] = _run_boat_sweep(engine, dry_run)
    # --- 3. NULL misleading attributions on open classes ------------------
    report["null_sweep"] = _run_null_sweep(engine, dry_run)

    with engine.begin() as conn:
        after = boats_design_stats(conn)
        report["after"] = after
        record_boats_stats(conn, "after", after)

        for col, st in design_class_fill(conn).items():
            record_metric(
                conn,
                METRIC_DC_NULL_RATE,
                scope=col,
                phase="after",
                value_num=st["null_rate"],
                meta={"total": st["total"], "nulls": st["nulls"]},
            )

        fk = ensure_fk(conn, dry_run)
        fk_now = fk_state(conn)
        report["fk"] = {**fk, **fk_now}
        record_metric(
            conn,
            METRIC_FK,
            scope=FK_NAME,
            phase="after",
            value_num=1.0 if fk_now["validated"] else 0.0,
            value_text=fk["outcome"],
            meta={
                "exists": fk_now["exists"],
                "validated": fk_now["validated"],
                "definition": fk_now["definition"],
                "orphans": fk["orphans"],
                "dry_run": dry_run,
            },
        )

        for step in ("merge", "boat_sweep", "null_sweep"):
            record_metric(
                conn,
                METRIC_SWEEP,
                scope=step,
                phase="run",
                value_text="dry_run" if dry_run else "applied",
                meta=report[step],
            )

    return report


def _print(report: dict[str, Any]) -> None:
    tag = "[DRY RUN] " if report["dry_run"] else ""
    b, a = report["before"], report["after"]

    def _pct(x: float) -> str:
        return f"{100 * x:5.2f}%"

    print()
    print("=" * 72)
    print(f"{tag}OPS-02-09 design_classes sweep report")
    print("=" * 72)
    print(
        f"  boats.design_canonical NULL rate: "
        f"{_pct(b['null_rate'])} ({b['nulls']}/{b['total']})  ->  "
        f"{_pct(a['null_rate'])} ({a['nulls']}/{a['total']})"
    )
    print(
        f"  orphan boats (no design_classes row): "
        f"{b['orphan_boats']} -> {a['orphan_boats']} "
        f"(distinct {b['orphan_distinct']} -> {a['orphan_distinct']})"
    )
    print(
        f"  FK {FK_NAME}: outcome={report['fk']['outcome']} "
        f"validated={report['fk']['validated']}"
    )
    m = report["merge"]
    print(
        f"  merge: {m['clusters_merged']} clusters, "
        f"{m['rows_deleted']} rows deleted, "
        f"{m['boats_repointed']} boats re-pointed"
    )
    s = report["boat_sweep"]
    print(
        f"  boat sweep: {s['updates_applied']} updates applied "
        f"({s['updates_skipped_no_target']} skipped — no FK target)"
    )
    ns = report["null_sweep"]
    if ns.get("status") == "skipped":
        print(f"  null sweep: skipped ({ns['reason']})")
    else:
        print(
            f"  null sweep: {ns['candidates']} candidates, nulled={ns['nulled']}"
        )
    print()
    print("  design_classes attribute NULL rates (after):")
    engine = get_engine()
    with engine.connect() as conn:
        for col, st in design_class_fill(conn).items():
            print(f"    {col:<22} {_pct(st['null_rate']):>7}  ({st['nulls']}/{st['total']})")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; no DB writes")
    args = ap.parse_args()

    engine = get_engine()
    report = run(engine, dry_run=args.dry_run)
    _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
