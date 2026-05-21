"""DNF status derivation for SailSys-shaped race results.

Bug context: SUN FISH B2G 2026 (boat_id 12330) was stored with status='finished'
even though SailSys returned finish_time=None (DNF). Every legacy SailSys row
with a missing finish_time had the same wrong status because result_import.py
hardcoded status='finished'.
"""

import pytest

from irc_data.scrapers.result_import import _derive_status


class TestNullFinishTime:
    def test_finish_time_key_present_but_null_is_dnf(self):
        # SailSys shape: scraper always sets the key; None means DNF.
        raw = {
            "boat_name": "SUN FISH",
            "sail_number": "3375",
            "finish_time": None,
            "scratch_place": 25,
        }
        assert _derive_status(raw, place=None) == "DNF"

    def test_finish_time_empty_string_is_dnf(self):
        raw = {"boat_name": "X", "finish_time": ""}
        assert _derive_status(raw, place=None) == "DNF"

    def test_null_finish_time_with_place_is_finished(self):
        # SailSys sometimes omits raw finish_time but the API computed a
        # corrected-time place — the boat did finish.
        raw = {"boat_name": "X", "finish_time": None}
        assert _derive_status(raw, place=12) == "finished"

    def test_explicit_dnf_overrides_populated_place(self):
        # Explicit status marker wins even if place got computed somehow.
        raw = {"boat_name": "X", "finish_time": None, "status": "DNF"}
        assert _derive_status(raw, place=12) == "DNF"


class TestFinishedRows:
    def test_finish_time_populated_is_finished(self):
        raw = {
            "boat_name": "X",
            "sail_number": "1",
            "finish_time": "2026-04-12T15:42:11",
        }
        assert _derive_status(raw, place=1) == "finished"


class TestExplicitStatusMarkers:
    @pytest.mark.parametrize(
        "marker", ["DNF", "DNS", "DNC", "DSQ", "RET", "OCS", "RAF", "RDG", "ZFP"]
    )
    def test_explicit_status_respected(self, marker):
        raw = {"boat_name": "X", "finish_time": "10:00:00", "status": marker}
        assert _derive_status(raw, place=None) == marker

    def test_explicit_status_case_insensitive(self):
        raw = {"boat_name": "X", "finish_time": None, "status": "dns"}
        assert _derive_status(raw, place=None) == "DNS"

    def test_unknown_explicit_status_falls_through(self):
        # Random unknown string should not override the finish_time logic.
        raw = {"boat_name": "X", "finish_time": None, "status": "MAYBE"}
        assert _derive_status(raw, place=None) == "DNF"


class TestBackwardCompatibility:
    def test_no_finish_time_key_defaults_finished(self):
        # Scrapers that don't track finish_time at all should not regress
        # to mass-DNF labelling.
        raw = {"boat_name": "X", "sail_number": "1"}
        assert _derive_status(raw, place=3) == "finished"

    def test_none_raw_data_defaults_finished(self):
        assert _derive_status(None, place=1) == "finished"

    def test_empty_raw_data_defaults_finished(self):
        assert _derive_status({}, place=None) == "finished"
