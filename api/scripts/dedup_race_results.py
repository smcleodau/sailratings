"""Find (and optionally remove) duplicate race_results under the natural key.

Replays ``upsert_race_result``'s matching (``irc_data.db.result_identity``)
over the stored rows in id order: within each scope, a row that the save path
would now have recognised as an already-stored result joins that result's
group. Groups are reported per source.

Cleanup rules (``--apply``):
  * only sources listed in ``--sources``;
  * only groups whose rows come from *different* scrape runs (created_at at
    least 10 minutes apart). Collisions inside one run are distinct rows the
    scraper did not label apart (e.g. two tables on one RHKYC page) and are
    reported, never deleted;
  * keep the newest row of each group that carries a rating (latest scrape); if it lost the boat
    link an older copy had, carry ``boat_id``/``event_entry_id`` over from
    the newest linked copy — the same "never drop a link" rule the save path
    applies;
  * back up every deleted row to ``<backup>`` and every kept row that gets
    modified to ``<backup>_kept`` first, then delete in the same transaction.

Usage::

    python scripts/dedup_race_results.py                      # report only
    python scripts/dedup_race_results.py --apply \
        --sources isora,rhkyc,sailracehq,rorc,sydneyhobart,cowesweek,yachtscoring \
        --backup-table race_results_backup_dedup_20261103
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import text

from irc_data.db.connection import get_engine
from irc_data.db.result_identity import SCOPE_COLUMNS, ResultIdentity, pick_existing

SAME_RUN = timedelta(minutes=10)


def _ident(r, id=None) -> ResultIdentity:
    return ResultIdentity.build(
        id=r.id if id is None else id, boat_name=r.boat_name, sail_number=r.sail_number,
        place=r.place, status=r.status, boat_id=r.boat_id, source_url=r.source_url,
    )


def find_groups(conn):
    cols = ", ".join(f"r.{c}" for c in SCOPE_COLUMNS)
    rows = conn.execute(text(f"""
        SELECT r.id, {cols}, r.place, r.status, r.boat_id, r.event_entry_id,
               r.source_url, r.created_at, r.tcc_at_race,
               COALESCE(r.raw_data->>'boat_name', ee.boat_name) AS boat_name,
               COALESCE(r.raw_data->>'sail_number', r.raw_data->>'sail_no',
                        ee.sail_number) AS sail_number
        FROM race_results r LEFT JOIN event_entries ee ON ee.id = r.event_entry_id
        ORDER BY r.id
    """)).fetchall()
    scopes = defaultdict(list)
    for r in rows:
        scopes[tuple(getattr(r, c) for c in SCOPE_COLUMNS)].append(r)

    groups = []
    for members in scopes.values():
        if len(members) < 2:
            continue
        # Like the save path, a row is compared with every stored row of the
        # scope (not just each group's latest), so chains such as
        # PINK PANTHER -> PINK PANTHER -> PANTHER (same sail, same place)
        # stay one group regardless of which copies still exist.
        clusters: list[list] = []
        owner: dict[int, int] = {}
        for r in members:
            stored = [_ident(x) for c in clusters for x in c]
            hit = pick_existing(_ident(r), stored)
            if hit is None:
                owner[r.id] = len(clusters)
                clusters.append([r])
            else:
                owner[r.id] = owner[hit.id]
                clusters[owner[hit.id]].append(r)
        groups.extend(c for c in clusters if len(c) > 1)
    return len(rows), groups


def plan(groups, sources: set[str]):
    stats = defaultdict(lambda: defaultdict(int))
    deletes: list[int] = []
    relinks: dict[int, tuple[int, int]] = {}
    for c in groups:
        src = c[0].source
        s = stats[src]
        s["groups"] += 1
        s["extra_rows"] += len(c) - 1
        times = sorted(x.created_at for x in c)
        if any(b - a < SAME_RUN for a, b in zip(times, times[1:])):
            s["same_run_groups_left"] += 1
            s["same_run_rows_left"] += len(c) - 1
            continue
        # Prefer a copy that carries the IRC rating, then the latest scrape.
        keep = max(c, key=lambda x: (x.tcc_at_race is not None, x.created_at, x.id))
        drop = [x for x in c if x.id != keep.id]
        s["cross_run_groups"] += 1
        s["cross_run_rows"] += len(drop)
        if src not in sources:
            continue
        deletes.extend(x.id for x in drop)
        if keep.boat_id is None:
            linked = [x for x in drop if x.boat_id is not None]
            if linked:
                donor = max(linked, key=lambda x: (x.created_at, x.id))
                relinks[keep.id] = (donor.boat_id, donor.event_entry_id)
    return stats, sorted(deletes), relinks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sources", default="", help="comma list of sources to clean")
    ap.add_argument("--apply", action="store_true", help="back up and delete")
    ap.add_argument("--backup-table", default=None)
    args = ap.parse_args()
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}

    engine = get_engine()
    with engine.begin() as conn:
        n, groups = find_groups(conn)
        stats, deletes, relinks = plan(groups, sources)
        print(f"rows scanned: {n}")
        for src in sorted(stats):
            mark = " (cleanup)" if src in sources else ""
            print(f"  {src}{mark}: {dict(stats[src])}")
        print(f"rows to delete: {len(deletes)}; kept rows to relink: {len(relinks)}")
        if not args.apply:
            print("dry run — nothing changed")
            return
        if not deletes:
            return
        backup = args.backup_table
        if not backup or not re.fullmatch(r"[a-z_][a-z0-9_]*", backup):
            raise SystemExit("--backup-table (lowercase identifier) is required with --apply")

        conn.execute(text(
            f"CREATE TABLE {backup} AS SELECT * FROM race_results WHERE id = ANY(:ids)"
        ), {"ids": deletes})
        backed = conn.execute(text(f"SELECT count(*) FROM {backup}")).scalar_one()
        if backed != len(deletes):
            raise SystemExit(f"backup count {backed} != {len(deletes)}; rolled back")
        if relinks:
            conn.execute(text(
                f"CREATE TABLE {backup}_kept AS SELECT * FROM race_results WHERE id = ANY(:ids)"
            ), {"ids": list(relinks)})
            for kid, (boat_id, entry_id) in relinks.items():
                conn.execute(text(
                    "UPDATE race_results SET boat_id = :b, event_entry_id = :e "
                    "WHERE id = :i AND boat_id IS NULL"
                ), {"b": boat_id, "e": entry_id, "i": kid})
        deleted = conn.execute(
            text("DELETE FROM race_results WHERE id = ANY(:ids)"), {"ids": deletes}
        ).rowcount
        if deleted != len(deletes):
            raise SystemExit(f"deleted {deleted} != {len(deletes)}; rolled back")
        print(f"backed up {backed} rows to {backup}; relinked {len(relinks)} kept rows "
              f"(prior state in {backup}_kept); deleted {deleted}")


if __name__ == "__main__":
    main()
