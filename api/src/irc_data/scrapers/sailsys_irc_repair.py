"""Repair SailSys race_results stored before the IRC-definition fix (b2e43e4).

The pre-fix parser only treated handicap definition ids {1, 33} as IRC, so
races scored on any other IRC definition (CYCA's id 25, ...) were stored with
PHS as ``tcc_at_race``/``rating_value``/``place`` and no ``irc_*`` keys in
``raw_data``. This module re-parses each race payload twice, once with the
legacy id set and once with per-race detection, and treats a stored row as
affected only where the two parses disagree. That isolates exactly the rows
the bug touched; differences caused by SailSys edits since import are left
alone.

Columns written are the ones ``import_scraper_results`` derives from the IRC
parse: ``tcc_at_race``, ``rating_value``, ``rating_type``, ``place``,
``status`` (only when its derivation from place changes and the stored value
is still the derived one) and the ``irc_tcc``/``irc_place``/``irc_corrected``
keys of ``raw_data``. ``corrected_time`` is left alone: the importer never
writes it for SailSys (the corrected time lives in ``raw_data``).
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from irc_data.parsers.schemas import RaceResult
from irc_data.scrapers.result_import import _derive_status
from irc_data.scrapers.sailsys import irc_definition_ids, parse_race_payload

LEGACY_IRC_IDS = frozenset({1, 33})
RAW_IRC_KEYS = ("irc_tcc", "irc_place", "irc_corrected")
BACKUP_TABLE = "race_results_backup_sailsys_irc_20261103"

# Identity keys, least to most specific. raw_data stores exactly what the
# parser emitted, so exact comparison is right (no normalisation).
_KEY_LEVELS = (
    ("boat_name", "sail_number"),
    ("boat_name", "sail_number", "division"),
    ("boat_name", "sail_number", "division", "skipper"),
)


def _used_definition_ids(race_data: dict) -> set[int]:
    ids = {(d or {}).get("id") for d in race_data.get("handicappings") or []}
    for group in race_data.get("competitors") or []:
        for item in group.get("items") or []:
            item = item or {}
            for hc in (item.get("handicap") or {}).get("currentHandicaps") or []:
                ids.add(((hc or {}).get("definition") or {}).get("id"))
            for calc in item.get("calculations") or []:
                ids.add((calc or {}).get("handicapDefinitionId"))
    ids.discard(None)
    return ids


def new_irc_ids(race_data: dict) -> set[int]:
    """IRC definition ids used in this race that the old parser missed.

    ``irc_definition_ids`` always includes the static list (now with 25), so
    intersect with the ids the payload actually uses.
    """
    return (irc_definition_ids(race_data) & _used_definition_ids(race_data)) - LEGACY_IRC_IDS


def _key(raw: dict, level: tuple[str, ...]) -> tuple:
    return tuple(raw.get(k) for k in level)


def _fields(r: RaceResult) -> dict:
    raw = r.raw_data or {}
    tcc = r.tcc_at_race
    return {
        "tcc_at_race": tcc,
        "rating_value": tcc,
        "rating_type": "irc_tcc" if tcc else None,
        "place": r.place,
        "status": _derive_status(raw, place=r.place),
        **{k: raw.get(k) for k in RAW_IRC_KEYS},
    }


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v)).normalize()


def _same(a: dict, b: dict, keys) -> bool:
    for k in keys:
        x, y = a.get(k), b.get(k)
        if k in ("tcc_at_race", "rating_value"):
            x, y = _dec(x), _dec(y)
        if x != y:
            return False
    return True


_COMPARE = ("tcc_at_race", "rating_value", "rating_type", "place", *RAW_IRC_KEYS)


@dataclass
class RacePlan:
    race_id: int
    updates: list[dict] = field(default_factory=list)  # {id, before, after}
    unmatched: list[dict] = field(default_factory=list)  # {boat_name, reason}
    already_ok: int = 0


def stored_fields(row: dict) -> dict:
    raw = row.get("raw_data") or {}
    return {
        "tcc_at_race": row.get("tcc_at_race"),
        "rating_value": row.get("rating_value"),
        "rating_type": row.get("rating_type"),
        "place": row.get("place"),
        "status": row.get("status"),
        **{k: raw.get(k) for k in RAW_IRC_KEYS},
    }


def plan_race(race_data: dict, stored_rows: list[dict], club_id: int, series_id: int,
              race_id: int) -> RacePlan:
    """Work out the updates for one race.

    ``stored_rows`` are this race's race_results rows as dicts with ``id``,
    the rating columns and ``raw_data``.
    """
    plan = RacePlan(race_id)
    if not new_irc_ids(race_data):
        return plan
    old = parse_race_payload(race_data, club_id, series_id, race_id, irc_ids=set(LEGACY_IRC_IDS))
    new = parse_race_payload(race_data, club_id, series_id, race_id)

    for i, (o, n) in enumerate(zip(old, new)):
        of, nf = _fields(o), _fields(n)
        if _same(of, nf, _COMPARE):
            continue  # the bug didn't touch this entrant
        raw = n.raw_data or {}
        # Least specific identity that is unique among the parsed entrants.
        level = next((lv for lv in _KEY_LEVELS
                      if sum(_key(x.raw_data or {}, lv) == _key(raw, lv) for x in new) == 1), None)
        if level is None:
            plan.unmatched.append({"boat_name": raw.get("boat_name"),
                                   "reason": "ambiguous entrant identity in payload"})
            continue
        rows = [r for r in stored_rows if _key(r.get("raw_data") or {}, level) == _key(raw, level)]
        if not rows:
            plan.unmatched.append({"boat_name": raw.get("boat_name"),
                                   "reason": "no stored row for entrant"})
            continue
        for row in rows:
            before = stored_fields(row)
            if _same(before, nf, _COMPARE):
                plan.already_ok += 1
                continue
            after = dict(nf)
            # Status: only follow the place change when the stored status is
            # still what the importer derived (later status backfills win).
            if not (of["status"] != nf["status"] and before["status"] == of["status"]):
                after["status"] = before["status"]
            plan.updates.append({"id": row["id"], "before": before, "after": after})
    return plan


# ---------------------------------------------------------------------------
# Payload cache: scan once, plan/dry-run/apply from disk.
# ---------------------------------------------------------------------------


class PayloadCache:
    """Fetched race payloads plus a manifest of every race scanned, with the
    IRC definitions the old parser missed (so scans resume)."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest = root / "manifest.jsonl"

    def scanned(self) -> dict[int, dict]:
        out = {}
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    out[rec["race_id"]] = rec
        return out

    def record(self, race_id: int, race_data: dict | None) -> dict:
        rec: dict = {"race_id": race_id}
        if race_data is None:
            rec["status"] = "http_error"
        else:
            extra = sorted(new_irc_ids(race_data))
            rec["status"] = "ok"
            rec["new_irc_ids"] = extra
            # Keep every payload (a few KB gzipped) so plans can be rebuilt
            # without re-fetching.
            with gzip.open(self.root / f"race_{race_id}.json.gz", "wt") as f:
                json.dump(race_data, f)
        with self.manifest.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def load(self, race_id: int) -> dict | None:
        p = self.root / f"race_{race_id}.json.gz"
        if not p.exists():
            return None
        with gzip.open(p, "rt") as f:
            return json.load(f)


def group_rows_by_race(rows) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[int((r["raw_data"] or {})["race_id"])].append(r)
    return out
