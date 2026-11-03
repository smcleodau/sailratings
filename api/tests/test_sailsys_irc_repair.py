"""Repair plan for SailSys rows stored by the pre-b2e43e4 parser (IRC ids {1, 33})."""

import json
from decimal import Decimal
from pathlib import Path

from irc_data.scrapers.sailsys import parse_race_payload
from irc_data.scrapers.sailsys_irc_repair import LEGACY_IRC_IDS, PayloadCache, plan_race

FX = Path(__file__).parent / "fixtures" / "sailsys" / "race_43879_cyca_irc25.json"


def _race():
    return json.loads(FX.read_text())["data"]


def _stored_as_old_parser(race):
    """Rows as import_scraper_results stored them before the fix."""
    rows = []
    for i, r in enumerate(parse_race_payload(race, 3, 5924, 43879, irc_ids=set(LEGACY_IRC_IDS))):
        rows.append({"id": 1000 + i, "tcc_at_race": r.tcc_at_race, "rating_value": r.tcc_at_race,
                     "rating_type": "irc_tcc" if r.tcc_at_race else None, "place": r.place,
                     "status": "finished", "raw_data": r.raw_data})
    return rows


def test_plan_fixes_cyca_irc_rows_only():
    race = _race()
    stored = _stored_as_old_parser(race)
    plan = plan_race(race, stored, 3, 5924, 43879)
    by_name = {next(r for r in stored if r["id"] == u["id"])["raw_data"]["boat_name"]: u
               for u in plan.updates}

    about = by_name["About Time"]
    assert about["before"]["place"] == 2 and about["before"]["irc_tcc"] is None
    assert about["after"]["tcc_at_race"] == Decimal("1.221")
    assert about["after"]["place"] == 1 and about["after"]["irc_place"] == 1
    assert about["after"]["irc_corrected"] == "06:01:40"
    assert about["after"]["rating_type"] == "irc_tcc"
    assert "Cadenza" not in by_name  # PHS-only entrant untouched
    assert plan.unmatched == []

    # Re-planning against already-repaired rows is a no-op.
    for u in plan.updates:
        row = next(r for r in stored if r["id"] == u["id"])
        a = u["after"]
        row.update(tcc_at_race=a["tcc_at_race"], rating_value=a["rating_value"], place=a["place"])
        row["raw_data"] = {**row["raw_data"], **{k: a[k] for k in ("irc_tcc", "irc_place", "irc_corrected")}}
    again = plan_race(race, stored, 3, 5924, 43879)
    assert again.updates == [] and again.already_ok == len(plan.updates)


def test_plan_skips_races_without_missed_definitions_and_reports_missing_rows():
    race = _race()
    legacy_only = {**race, "handicappings": [{"id": 5, "shortName": "PHS"}],
                   "competitors": []}
    assert plan_race(legacy_only, [], 3, 5924, 1).updates == []

    plan = plan_race(race, [], 3, 5924, 43879)
    assert plan.updates == [] and plan.unmatched
    assert {u["reason"] for u in plan.unmatched} == {"no stored row for entrant"}


def test_payload_cache_records_and_resumes(tmp_path):
    cache = PayloadCache(tmp_path)
    cache.record(43879, _race())
    cache.record(1, {"handicappings": [{"id": 5, "shortName": "PHS"}], "competitors": []})
    cache.record(2, None)
    seen = cache.scanned()
    assert seen[43879]["new_irc_ids"] == [25] and seen[1]["new_irc_ids"] == []
    assert seen[2]["status"] == "http_error"
    assert cache.load(43879)["name"] and cache.load(2) is None


def test_curly_quote_or_case_rename_still_matches_stored_row():
    race = _race()
    stored = _stored_as_old_parser(race)
    row = next(r for r in stored if r["raw_data"]["boat_name"] == "About Time")
    row["raw_data"] = {**row["raw_data"], "boat_name": "ABOUT TIME "}
    plan = plan_race(race, stored, 3, 5924, 43879)
    assert row["id"] in {u["id"] for u in plan.updates} and plan.unmatched == []
