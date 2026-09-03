"""Sail-number boat merge routine, connection-scoped (AD-01-14).

The proven merge machinery lives in ``api/scripts/merge_boat_dupes_medium.py``
(engine-scoped ``merge_cluster`` with per-cluster transaction, FK-aware
re-point helpers, collision resolution and the ``boat_merges`` audit write).
This module ports that machinery into the ``irc_data`` package so the admin
dupe-merge endpoint re-uses the *same* sail-number merge routine, with three
adaptations:

  * the winner is chosen by the human reviewer and passed in (the script
    elects its own winner),
  * everything runs inside the caller's transaction so the endpoint can
    bundle the queue-verdict and ``admin_edits`` audit writes atomically
    with the merge itself,
  * the re-point additionally covers the FK tables the one-shot scripts
    never needed: ``event_entries``, ``boat_events`` and
    ``boat_news_mentions`` (the AD-01-14 contract names all of:
    boat_identities, event_entries, irc_certificates, orc_certificates,
    tcc_snapshots, orders, boat_events, boat_news_mentions).

One ``boat_merges`` audit row is written per loser with the full loser row
plus collision extras as the ``loser_snapshot`` jsonb — the identical shape
the scripts write, so merge history reads one format regardless of which
surface performed the merge.

Dialect note: collision-prone re-points (``race_results``,
``boat_news_mentions``) delete rows the winner already duplicates instead of
moving them.  The SQL is ANSI/Postgres/SQLite portable so the transactional
tests run against in-memory SQLite fixtures while production runs Postgres.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


class MergeError(RuntimeError):
    """Raised when a merge cannot complete (the caller rolls back)."""


@dataclass
class BoatMergeReport:
    """Outcome of merging one or more losers into a winner."""

    winner_id: int
    loser_ids: list[int] = field(default_factory=list)
    rows_repointed: dict[str, int] = field(default_factory=dict)
    collisions_resolved: int = 0


# Tables re-pointed with a plain UPDATE (no UNIQUE constraint can conflict).
# Extends the script's SIMPLE_FK_TABLES with the tables named by the
# AD-01-14 contract that the one-shot passes never touched.
SIMPLE_REPOINT_TABLES: tuple[str, ...] = (
    "boat_identities",
    "event_entries",
    "orc_certificates",
    "orders",
    "insight_cache",
    "boat_corrections",
    "boat_events",
)

# Collision-prone tables: re-pointing can violate a UNIQUE constraint the
# winner already satisfies.  Such loser rows are deleted (their meaning is
# already carried by the winner's row) and recorded in the snapshot extras.
#
#   race_results        UNIQUE(boat_id, event_name, event_date)
#   boat_news_mentions  PRIMARY KEY(news_id, boat_id)
COLLISION_PRONE_TABLES: tuple[str, ...] = (
    "race_results",
    "boat_news_mentions",
)

_COLLISION_KEYS: dict[str, tuple[str, ...]] = {
    "race_results": ("event_name", "event_date"),
    "boat_news_mentions": ("news_id",),
}

# Post-merge verification: no row in any of these tables may still reference
# the loser before its boats row is deleted.
RESIDUAL_CHECK_TABLES: tuple[str, ...] = (
    "irc_certificates",
    "tcc_snapshots",
    *COLLISION_PRONE_TABLES,
    *SIMPLE_REPOINT_TABLES,
)


# ---------------------------------------------------------------------------
# Dialect helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: Connection, table: str) -> bool:
    """Dialect-portable existence check (Postgres + SQLite fixtures)."""
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).fetchone()
        return row is not None
    row = conn.execute(
        text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
    ).fetchone()
    return row is not None and row[0] is not None


def _id_in_clause(ids: list[int]) -> str:
    """Safe literal IN-list for small int sets (ids are ints by contract)."""
    return ", ".join(str(int(i)) for i in ids)


def _fetch_boat_row(conn: Connection, boat_id: int) -> dict[str, Any] | None:
    """Full boats row as a plain dict (row_to_json equivalent)."""
    row = conn.execute(
        text("SELECT * FROM boats WHERE id = :id"), {"id": boat_id}
    ).mappings().fetchone()
    return dict(row) if row is not None else None


def _fetch_rows(
    conn: Connection, table: str, boat_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(f"SELECT * FROM {table} WHERE boat_id = :b"), {"b": boat_id}
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Collision-aware re-points
# ---------------------------------------------------------------------------

def resolve_cert_collisions(
    conn: Connection, winner_id: int, loser_id: int, extras_sink: list[dict]
) -> tuple[int, int]:
    """Move loser IRC certificates to the winner, resolving cert_number clashes.

    When both boats hold the same cert_number the newer ``issue_date`` wins;
    the displaced row is deleted and preserved in the snapshot extras.
    Mirrors ``merge_boat_dupes_medium.resolve_cert_collisions``.
    """
    if not _table_exists(conn, "irc_certificates"):
        return 0, 0

    winner_certs = {
        r["cert_number"]: r
        for r in _fetch_rows(conn, "irc_certificates", winner_id)
        if r.get("cert_number") is not None
    }
    repointed = 0
    collisions = 0
    for lc in _fetch_rows(conn, "irc_certificates", loser_id):
        cn = lc.get("cert_number")
        wc = winner_certs.get(cn) if cn is not None else None
        if wc is None:
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
            continue

        winner_date = wc.get("issue_date")
        loser_date = lc.get("issue_date")
        winner_is_newer = (
            winner_date >= loser_date
            if (winner_date is not None and loser_date is not None)
            else True
        )
        if winner_is_newer:
            extras_sink.append({"kind": "certificate_collision_dropped", "row": lc})
            conn.execute(
                text("DELETE FROM irc_certificates WHERE id = :id"), {"id": lc["id"]}
            )
        else:
            extras_sink.append(
                {"kind": "certificate_collision_replaced_winner", "row": wc}
            )
            conn.execute(
                text("DELETE FROM irc_certificates WHERE id = :id"), {"id": wc["id"]}
            )
            conn.execute(
                text("UPDATE irc_certificates SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": lc["id"]},
            )
            repointed += 1
        collisions += 1
    return repointed, collisions


def resolve_tcc_collisions(
    conn: Connection, winner_id: int, loser_id: int, extras_sink: list[dict]
) -> tuple[int, int]:
    """Move loser TCC snapshots to the winner, resolving snapshot_date clashes.

    On a clash the higher ``cert_year`` wins; the displaced row is deleted
    and preserved in the snapshot extras.  Mirrors the script equivalent.
    """
    if not _table_exists(conn, "tcc_snapshots"):
        return 0, 0

    winner_snaps = {r["snapshot_date"]: r for r in _fetch_rows(conn, "tcc_snapshots", winner_id)}
    repointed = 0
    collisions = 0
    for ls in _fetch_rows(conn, "tcc_snapshots", loser_id):
        ws = winner_snaps.get(ls.get("snapshot_date"))
        if ws is None:
            conn.execute(
                text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": ls["id"]},
            )
            repointed += 1
            continue

        winner_year = ws.get("cert_year") or 0
        loser_year = ls.get("cert_year") or 0
        if winner_year >= loser_year:
            extras_sink.append({"kind": "tcc_snapshot_collision_dropped", "row": ls})
            conn.execute(
                text("DELETE FROM tcc_snapshots WHERE id = :id"), {"id": ls["id"]}
            )
        else:
            extras_sink.append(
                {"kind": "tcc_snapshot_collision_replaced_winner", "row": ws}
            )
            conn.execute(
                text("DELETE FROM tcc_snapshots WHERE id = :id"), {"id": ws["id"]}
            )
            conn.execute(
                text("UPDATE tcc_snapshots SET boat_id = :w WHERE id = :id"),
                {"w": winner_id, "id": ls["id"]},
            )
            repointed += 1
        collisions += 1
    return repointed, collisions


def _repoint_deduped(
    conn: Connection, table: str, winner_id: int, loser_id: int,
    extras_sink: list[dict],
) -> tuple[int, int]:
    """Re-point a UNIQUE-constrained table, deleting rows the winner duplicates.

    Returns (rows_repointed, collisions_resolved).
    """
    keys = _COLLISION_KEYS[table]
    winner_keys = {
        tuple(str(w.get(k)) for k in keys) for w in _fetch_rows(conn, table, winner_id)
    }

    repointed = 0
    collisions = 0
    for row in _fetch_rows(conn, table, loser_id):
        row_key = tuple(str(row.get(k)) for k in keys)
        where = (
            "id = :id"
            if "id" in row
            else "boat_id = :l AND " + " AND ".join(f"{k} = :{k}" for k in keys)
        )
        params = (
            {"id": row["id"]}
            if "id" in row
            else {"l": loser_id, **{k: row.get(k) for k in keys}}
        )
        if row_key in winner_keys:
            extras_sink.append({"kind": f"{table}_collision_dropped", "row": row})
            conn.execute(text(f"DELETE FROM {table} WHERE {where}"), params)
            collisions += 1
            continue
        if "id" not in row:
            params["w"] = winner_id
            conn.execute(
                text(f"UPDATE {table} SET boat_id = :w WHERE {where}"), params
            )
        else:
            conn.execute(
                text(f"UPDATE {table} SET boat_id = :w WHERE {where}"),
                {"w": winner_id, **params},
            )
        repointed += 1
    return repointed, collisions


def _repoint_simple(conn: Connection, table: str, winner_id: int, loser_id: int) -> int:
    res = conn.execute(
        text(f"UPDATE {table} SET boat_id = :w WHERE boat_id = :l"),
        {"w": winner_id, "l": loser_id},
    )
    return res.rowcount or 0


# ---------------------------------------------------------------------------
# The merge routine
# ---------------------------------------------------------------------------

def merge_boat_records(
    conn: Connection,
    cluster_key: str,
    winner_id: int,
    loser_ids: list[int],
) -> BoatMergeReport:
    """Merge ``loser_ids`` into ``winner_id`` inside the caller's transaction.

    Per loser: resolve cert/TCC collisions, re-point every FK table
    (boat_identities, event_entries, irc_certificates, orc_certificates,
    tcc_snapshots, orders, boat_events, boat_news_mentions, plus
    race_results / insight_cache / boat_corrections), write one
    ``boat_merges`` audit row carrying the full loser snapshot, verify no
    residual references remain, then delete the loser boat row.

    Raises :class:`MergeError` on any inconsistency; the caller rolls back.
    """
    if winner_id in loser_ids:
        raise MergeError("winner_id cannot also be a loser")

    existing = {
        r[0]
        for r in conn.execute(
            text(f"SELECT id FROM boats WHERE id IN ({_id_in_clause([winner_id, *loser_ids])})")
        ).fetchall()
    }
    if winner_id not in existing:
        raise MergeError(f"winner boat {winner_id} does not exist")
    for loser_id in loser_ids:
        if loser_id not in existing:
            raise MergeError(f"loser boat {loser_id} does not exist")

    rep = BoatMergeReport(winner_id=winner_id)

    for loser_id in loser_ids:
        extras: list[dict] = []
        loser_full = _fetch_boat_row(conn, loser_id)
        if loser_full is None:
            raise MergeError(f"loser {loser_id} vanished mid-merge")

        cert_rp, cert_col = resolve_cert_collisions(conn, winner_id, loser_id, extras)
        tcc_rp, tcc_col = resolve_tcc_collisions(conn, winner_id, loser_id, extras)
        rep.rows_repointed["irc_certificates"] = (
            rep.rows_repointed.get("irc_certificates", 0) + cert_rp
        )
        rep.rows_repointed["tcc_snapshots"] = (
            rep.rows_repointed.get("tcc_snapshots", 0) + tcc_rp
        )
        rep.collisions_resolved += cert_col + tcc_col

        for tbl in COLLISION_PRONE_TABLES:
            if not _table_exists(conn, tbl):
                continue
            rp, col = _repoint_deduped(conn, tbl, winner_id, loser_id, extras)
            rep.rows_repointed[tbl] = rep.rows_repointed.get(tbl, 0) + rp
            rep.collisions_resolved += col

        for tbl in SIMPLE_REPOINT_TABLES:
            if not _table_exists(conn, tbl):
                continue
            n = _repoint_simple(conn, tbl, winner_id, loser_id)
            rep.rows_repointed[tbl] = rep.rows_repointed.get(tbl, 0) + n

        snapshot = {"boat": loser_full, "extras": extras}
        conn.execute(
            text(
                "INSERT INTO boat_merges (winner_id, loser_id, cluster_key, loser_snapshot) "
                "VALUES (:w, :l, :ck, :snap)"
                if conn.dialect.name == "sqlite"
                else
                "INSERT INTO boat_merges (winner_id, loser_id, cluster_key, loser_snapshot) "
                "VALUES (:w, :l, :ck, CAST(:snap AS jsonb))"
            ),
            {
                "w": winner_id,
                "l": loser_id,
                "ck": cluster_key,
                "snap": json.dumps(snapshot, default=str),
            },
        )

        for tbl in RESIDUAL_CHECK_TABLES:
            if not _table_exists(conn, tbl):
                continue
            remaining = conn.execute(
                text(f"SELECT COUNT(*) FROM {tbl} WHERE boat_id = :l"),
                {"l": loser_id},
            ).scalar()
            if remaining:
                raise MergeError(
                    f"residual references in {tbl} for loser {loser_id}: {remaining}"
                )

        conn.execute(text("DELETE FROM boats WHERE id = :id"), {"id": loser_id})
        rep.loser_ids.append(loser_id)

    return rep
