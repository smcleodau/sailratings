#!/usr/bin/env python3
"""End-to-end verification evidence for OPS-02-14 — UK/Solent coverage.

Acceptance criterion (from the issue):
    Sun Fast 3300 and J/109 Solent fleets have >= 1 season of results in
    ``race_results``.

Verification is a **coverage query per fleet**: for each target design we
count distinct seasons (event years) and rows in ``race_results``, restricted
to Solent/UK sources (the ones OPS-02-14 scheduled: ``yachtscoring`` and the
Solent sources ``jog`` / ``warsash-spring-series`` / ``hamble-winter-series``,
plus the already-ingested Solent-relevant ``rorc`` / ``cowesweek`` /
``sailracehq`` / ``isora`` rows).

Also verifies the two other deliverables:
  * YachtScoring is scheduled (register row enabled+approved, nightly cadence,
    active ``source-yachtscoring`` schedule state).
  * The Solent sources are registered in ``data_sources`` with policy checks
    (``legal_status='approved'``, scheduling fields populated).

Prints paste-able PASS/FAIL evidence and exits non-zero on any failure.

Usage::

    PYTHONPATH=src python3 scripts/verify_ops_02_14.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402

from irc_data.db.connection import get_engine  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

#: Design-canonical values that count as each target fleet.  The fleet is the
#: set of boats whose canonical design is a Sun Fast 3300 (any of its DB
#: variants) or a J/109.
SF3300_DESIGNS = ("Sunfast 3300", "Jeanneau Sunfast 3300", "Jeanneau  Sunfast 3300")
J109_DESIGNS = ("J/109", "J Boats J/109")

#: Sources that publish Solent / UK-Solent results.  OPS-02-14 added the
#: Solent-specific ones; rorc / cowesweek / sailracehq / isora already carry
#: Solent fleets.
SOLENT_SOURCES = (
    "yachtscoring",
    "jog",
    "warsash-spring-series",
    "hamble-winter-series",
    "rorc",
    "cowesweek",
    "sailracehq",
    "isora",
)

#: Sources OPS-02-14 registered with policy checks.
NEW_SOLENT_SOURCES = ("jog", "warsash-spring-series", "hamble-winter-series")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _in_clause(vals) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in vals) + ")"


def main() -> int:
    engine = get_engine()

    print("=" * 78)
    print("OPS-02-14 verification — UK/Solent coverage")
    print("=" * 78)

    # ------------------------------------------------------------------
    print("\n0. Solent sources registered in data_sources with policy checks")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT slug, legal_status, enabled, cadence, cadence_class, "
                "staleness_budget_hours, robots_status, licensing "
                "FROM data_sources WHERE slug IN " + _in_clause(NEW_SOLENT_SOURCES)
            )
        ).fetchall()
    by_slug = {r[0]: r for r in rows}
    for slug in NEW_SOLENT_SOURCES:
        r = by_slug.get(slug)
        check(
            f"data_sources row: {slug}",
            r is not None,
            "present" if r else "MISSING",
        )
        if r:
            check(
                f"{slug} policy-checked (approved+enabled+scheduling)",
                r[1] == "approved" and r[2] and r[4] is not None and r[5] is not None,
                f"legal={r[1]} enabled={r[2]} cadence={r[3]} class={r[4]} budget={r[5]}",
            )

    # ------------------------------------------------------------------
    print("\n1. YachtScoring scheduled")
    with engine.connect() as conn:
        ds = conn.execute(
            text(
                "SELECT legal_status, enabled, cadence FROM data_sources "
                "WHERE slug='yachtscoring'"
            )
        ).first()
        sched = conn.execute(
            text(
                "SELECT paused, cadence, notes FROM source_schedule_state "
                "WHERE source_slug='yachtscoring'"
            )
        ).first()
    check(
        "yachtscoring register row approved + enabled",
        ds is not None and ds[0] == "approved" and ds[1],
        f"legal={ds[0] if ds else None} enabled={ds[1] if ds else None}",
    )
    check(
        "yachtscoring has an active (unpaused) schedule",
        sched is not None and not sched[0],
        f"paused={sched[0] if sched else None} cadence={sched[1] if sched else None}",
    )

    # ------------------------------------------------------------------
    print("\n2. Solent discovery ran (event_discovery queued Solent pages)")
    with engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM event_discovery WHERE "
                "source_url ILIKE ANY (ARRAY['%jog.org.uk%','%warsashsc.org.uk%',"
                "'%hamblewinterseries.com%','%halsail.com%','%hrsc.org.uk%'])"
            )
        ).scalar()
    check("Solent result pages discovered (event_discovery)", (n or 0) >= 1, f"{n} rows")

    # ------------------------------------------------------------------
    print("\n3. Fleet coverage — >= 1 season of results in race_results")
    for label, designs in (("Sun Fast 3300", SF3300_DESIGNS), ("J/109", J109_DESIGNS)):
        with engine.connect() as conn:
            # Seasons = distinct event years in which a fleet boat has a
            # result from a Solent source.
            row = conn.execute(
                text(
                    "SELECT count(*) AS rows, "
                    "count(DISTINCT EXTRACT(YEAR FROM r.event_date)) AS seasons, "
                    "count(DISTINCT r.boat_id) AS boats, "
                    "min(r.event_date) AS first, max(r.event_date) AS last "
                    "FROM race_results r JOIN boats b ON b.id = r.boat_id "
                    "WHERE b.design_canonical IN " + _in_clause(designs) + " "
                    "AND r.source IN " + _in_clause(SOLENT_SOURCES)
                )
            ).first()
            # Season breakdown for the detail line.
            per_season = conn.execute(
                text(
                    "SELECT EXTRACT(YEAR FROM r.event_date)::int AS yr, count(*) "
                    "FROM race_results r JOIN boats b ON b.id = r.boat_id "
                    "WHERE b.design_canonical IN " + _in_clause(designs) + " "
                    "AND r.source IN " + _in_clause(SOLENT_SOURCES) + " "
                    "GROUP BY 1 ORDER BY 1 DESC NULLS LAST LIMIT 6"
                )
            ).fetchall()
        seasons = int(row[1] or 0)
        rows_n = int(row[0] or 0)
        boats = int(row[2] or 0)
        detail = (
            f"{rows_n} rows, {boats} boats, {seasons} season(s) "
            f"[{row[3]}..{row[4]}]; by year: "
            + ", ".join(f"{s[0]}:{s[1]}" for s in per_season if s[0])
        )
        check(f"{label} Solent fleet >= 1 season of results", seasons >= 1 and rows_n > 0, detail)

    # ------------------------------------------------------------------
    print("\n4. New Solent-source rows landed (jog / warsash)")
    with engine.connect() as conn:
        for src in ("jog", "warsash-spring-series"):
            c = conn.execute(
                text("SELECT count(*), min(event_date), max(event_date) "
                     "FROM race_results WHERE source = :s"),
                {"s": src},
            ).first()
            check(
                f"race_results has {src} rows",
                (c[0] or 0) > 0,
                f"{c[0]} rows [{c[1]}..{c[2]}]",
            )

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    n_fail = len(RESULTS) - n_pass
    print(f"RESULT: {n_pass} passed, {n_fail} failed, {len(RESULTS)} checks")
    print("=" * 78)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
