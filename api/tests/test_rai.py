"""SM-01-03 golden fixtures: Racing Advantage Index with confidence intervals.

The golden fixture is a two-boat, eight-race series where HELD (boat 301,
TCC 1.000 — the low-rated boat, expected to win) beats CHASER (boat 302,
TCC 1.050) in every race.  In every race HELD's expected percentile is
rank 1/2 = 0.5 and CHASER's is rank 2/2 = 1.0, so every advantage
observation is exact:

    HELD:    A = (0.5 − 1/2) × 100 = 0.0   in all 8 races
    CHASER:  A = (1.0 − 2/2) × 100 = 0.0   in all 8 races

CHASER sails exactly *to* her rating — the handicap predicts her last place,
so a last-place finish is not under-performance.

Wind data is attached to HELD's races (4 × 6 kn light, 4 × 16 kn fresh) so
the TWS condition splits are pinned as well.

Verification covered here (per the issue's acceptance criteria):

* RAI with confidence interval per boat from corrected results
* class mean RAI (baseline aggregates threshold-passing boats only)
* condition splits by TWS band where wind data exists
* minimum-race threshold enforced (per boat and per band)
* reproducible per dataset version (bit-identical re-run; fingerprint
  changes iff the dataset changes)
* sensitivity to identity-merge errors (polluting HELD with CHASER's
  races flips the RAI by the pinned amount)
"""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.analysis.rai import (
    DEFAULT_CONFIG,
    RAI_CONFIG_SCHEMA,
    RAI_SCHEMA_VERSION,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    BoatInfo,
    RaceObservation,
    RAIRulesetConfigV1,
    class_baseline_from_results,
    class_baseline_v1,
    compute_rai_from_observations,
    compute_rai_v1,
    dataset_fingerprint,
    expected_percentile,
    extract_tws,
    tws_band_for,
)

# ---------------------------------------------------------------------------
# Golden fixture: HELD beats CHASER 8–0
# ---------------------------------------------------------------------------

N_RACES = 8
HELD_ID = 301
CHASER_ID = 302
HELD_TCC = 1.000
CHASER_TCC = 1.050
HELD_EXPECTED_PCT = 0.5  # lowest TCC ⇒ rank 1 of 2
CHASER_EXPECTED_PCT = 1.0  # highest TCC ⇒ rank 2 of 2

# Pinned advantage observations — both boats sail exactly to their rating.
GOLDEN_HELD_ADV = 0.0
GOLDEN_CHASER_ADV = 0.0

# HELD wind pattern: races 0–3 at 6 kn (light), races 4–7 at 16 kn (fresh).
LIGHT_TWS = 6.0
FRESH_TWS = 16.0


def _race_day(index: int) -> datetime.date:
    """``index`` distinct Saturdays in 2024 (Jan 6 2024 was a Saturday)."""
    return datetime.date(2024, 1, 6) + datetime.timedelta(days=7 * index)


def _seed_boat(conn, boat_id: int, name: str, sail: str, design: str) -> None:
    conn.execute(
        text(
            "INSERT INTO boats (id, boat_name, sail_number, design)"
            " VALUES (:id, :name, :sail, :design)"
        ),
        {"id": boat_id, "name": name, "sail": sail, "design": design},
    )


def _seed_result(
    conn,
    boat_id: int,
    event: str,
    race: str | None,
    day: datetime.date | None,
    place: int,
    fleet: int,
    rating: float,
    raw: dict | None = None,
    status: str = "finished",
) -> None:
    conn.execute(
        text(
            "INSERT INTO race_results"
            " (boat_id, event_name, race_name, event_date, place, fleet_size,"
            "  status, rating_value, raw_data)"
            " VALUES (:bid, :event, :race, :date, :place, :fleet, :status,"
            "         :rating, :raw)"
        ),
        {
            "bid": boat_id,
            "event": event,
            "race": race,
            "date": day.isoformat() if day else None,
            "place": place,
            "fleet": fleet,
            "status": status,
            "rating": rating,
            "raw": json.dumps(raw) if raw is not None else None,
        },
    )


def _seed_golden_series(conn) -> None:
    """Seed the HELD vs CHASER golden series (HELD wins all 8 races)."""
    for i in range(N_RACES):
        day = _race_day(i)
        tws = LIGHT_TWS if i < 4 else FRESH_TWS
        _seed_result(
            conn, HELD_ID, f"Event{i}", None, day,
            place=1, fleet=2, rating=HELD_TCC, raw={"tws": tws},
        )
        _seed_result(
            conn, CHASER_ID, f"Event{i}", None, day,
            place=2, fleet=2, rating=CHASER_TCC,
        )


def _seed_merge_error(conn) -> None:
    """Simulate an identity-merge error: CHASER's 8 second places are
    *additionally* keyed to HELD's resolved identity (at HELD's TCC, so each
    absorbed race has expected 1/2 = 0.5, actual 2/2 = 1.0 ⇒ A = −50)."""
    for i in range(N_RACES):
        day = _race_day(i)
        tws = LIGHT_TWS if i < 4 else FRESH_TWS
        _seed_result(
            conn, HELD_ID, f"Event{i}", None, day,
            place=2, fleet=2, rating=HELD_TCC, raw={"tws": tws},
        )


@pytest.fixture()
def golden_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT,"
            " sail_number TEXT, design TEXT, design_canonical TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE race_results (id INTEGER PRIMARY KEY,"
            " boat_id INTEGER, event_name TEXT, race_name TEXT,"
            " event_date DATE, race_number INTEGER, place INTEGER,"
            " fleet_size INTEGER, status TEXT DEFAULT 'finished',"
            " rating_value NUMERIC(8,4), corrected_time TEXT, raw_data TEXT)"
        ))
        _seed_boat(conn, HELD_ID, "HELD", "GBR101", "J/99")
        _seed_boat(conn, CHASER_ID, "CHASER", "GBR202", "J/99")
        _seed_golden_series(conn)
    return engine


def _golden_fields() -> dict[tuple[str, str | None, str | None], tuple[float, ...]]:
    return {
        (f"Event{i}", None, _race_day(i).isoformat()): (HELD_TCC, CHASER_TCC)
        for i in range(N_RACES)
    }


def _held_observations() -> list[RaceObservation]:
    return [
        RaceObservation(
            boat_id=HELD_ID,
            event_name=f"Event{i}",
            race_name=None,
            event_date=_race_day(i).isoformat(),
            place=1,
            fleet_size=2,
            rating_value=HELD_TCC,
            raw={"tws": LIGHT_TWS if i < 4 else FRESH_TWS},
        )
        for i in range(N_RACES)
    ]


# ---------------------------------------------------------------------------
# 1. RAI with confidence interval per boat from corrected results
# ---------------------------------------------------------------------------


class TestGoldenRAI:
    def test_held_rai_zero_with_degenerate_ci(self, golden_engine: Engine):
        res = compute_rai_v1(golden_engine, HELD_ID)
        assert res.schema == RAI_SCHEMA_VERSION
        assert res.status == STATUS_OK
        assert res.meets_min_races is True
        assert res.n_races == N_RACES
        assert res.n_scored == N_RACES
        assert res.rai == pytest.approx(GOLDEN_HELD_ADV, abs=1e-9)
        # Every observation identical ⇒ zero-width CI at the point estimate.
        assert res.ci_lower == pytest.approx(0.0, abs=1e-9)
        assert res.ci_upper == pytest.approx(0.0, abs=1e-9)
        assert res.ci_method in ("bootstrap-percentile", "degenerate")
        assert res.confidence_level == 0.95
        assert res.avg_finish_pct == pytest.approx(0.5, abs=1e-9)
        assert res.avg_expected_pct == pytest.approx(HELD_EXPECTED_PCT, abs=1e-9)
        assert res.wins == N_RACES
        assert res.podiums == N_RACES

    def test_chaser_sails_to_her_rating(self, golden_engine: Engine):
        """The handicap predicts last place for the highest-rated boat —
        finishing last is therefore *not* under-performance (RAI = 0)."""
        res = compute_rai_v1(golden_engine, CHASER_ID)
        assert res.status == STATUS_OK
        assert res.rai == pytest.approx(GOLDEN_CHASER_ADV, abs=1e-9)
        assert res.ci_lower == pytest.approx(0.0, abs=1e-9)
        assert res.ci_upper == pytest.approx(0.0, abs=1e-9)
        assert res.avg_expected_pct == pytest.approx(CHASER_EXPECTED_PCT, abs=1e-9)
        assert res.wins == 0

    def test_per_race_contributions_pinned(self, golden_engine: Engine):
        res = compute_rai_v1(golden_engine, HELD_ID)
        assert len(res.race_contributions) == N_RACES
        for c in res.race_contributions:
            assert c["scored"] is True
            assert c["expected_pct"] == pytest.approx(HELD_EXPECTED_PCT, abs=1e-9)
            assert c["actual_pct"] == pytest.approx(0.5, abs=1e-9)
            assert c["advantage"] == pytest.approx(GOLDEN_HELD_ADV, abs=1e-9)

    def test_varied_advantage_ci_contains_mean_and_is_ordered(self):
        """Non-degenerate data: CI is ordered and brackets the mean."""
        # Boat at TCC 1.010 ⇒ rank 3 of 5 ⇒ expected 0.6 each race.
        # Places 1..5 across five same-field races ⇒ varied advantages.
        obs = [
            RaceObservation(
                boat_id=1, event_name="E", race_name=None,
                event_date="2024-01-06", place=p, fleet_size=5,
                rating_value=1.010,
            )
            for p in (1, 2, 3, 4, 5)
        ]
        fields = {
            ("E", None, "2024-01-06"): (0.990, 1.000, 1.010, 1.020, 1.030),
        }
        res = compute_rai_from_observations(
            obs, fields, info=BoatInfo(boat_id=1, boat_name="VARIED"),
        )
        assert res.status == STATUS_OK
        # advantages: (0.6 − p/5) × 100 for p in 1..5 → 40, 20, 0, −20, −40
        assert res.rai == pytest.approx(0.0, abs=1e-9)
        assert res.ci_lower < res.rai < res.ci_upper
        assert res.ci_lower < res.ci_upper
        assert res.ci_method == "bootstrap-t"

    def test_interpretation_bands(self, golden_engine: Engine):
        held = compute_rai_v1(golden_engine, HELD_ID)
        chaser = compute_rai_v1(golden_engine, CHASER_ID)
        # Zero-width CI at exactly 0.0 spans zero ⇒ "within noise".
        assert "within noise" in held.interpretation
        assert "within noise" in chaser.interpretation

    def test_interpretation_detects_real_outperformance(self):
        """Varied advantages all positive ⇒ CI wholly above zero."""
        # Boat (TCC 1.010, rank 3 of 5 ⇒ expected 0.6) finishes 1st or 2nd
        # every race ⇒ advantages (0.6−0.2)·100=40 and (0.6−0.4)·100=20.
        obs = [
            RaceObservation(
                boat_id=1, event_name=f"E{i}", race_name=None,
                event_date=f"2024-06-{i + 1:02d}", place=1 if i % 2 == 0 else 2,
                fleet_size=5, rating_value=1.010,
            )
            for i in range(6)
        ]
        fields = {
            (f"E{i}", None, f"2024-06-{i + 1:02d}"): (0.990, 1.000, 1.010, 1.020, 1.030)
            for i in range(6)
        }
        res = compute_rai_from_observations(obs, fields, info=BoatInfo(boat_id=1))
        assert res.status == STATUS_OK
        assert res.rai == pytest.approx(30.0, abs=1e-9)
        assert res.ci_lower > 0.0
        assert "Out-performing" in res.interpretation


# ---------------------------------------------------------------------------
# 2. Class mean RAI (baseline)
# ---------------------------------------------------------------------------


class TestClassBaseline:
    def test_class_mean_rai_is_mean_of_member_rais(self, golden_engine: Engine):
        baseline = class_baseline_v1(golden_engine, "J/99")
        assert baseline.schema == "ClassBaselineV1"
        assert baseline.n_boats == 2
        assert baseline.n_boats_total == 2
        assert baseline.mean_rai == pytest.approx(
            (GOLDEN_HELD_ADV + GOLDEN_CHASER_ADV) / 2.0, abs=1e-9
        )
        assert baseline.median_rai == pytest.approx(0.0, abs=1e-9)
        assert baseline.p25_rai <= baseline.median_rai <= baseline.p75_rai
        assert baseline.min_races_required == DEFAULT_CONFIG.min_races

    def test_under_threshold_boats_excluded_from_baseline(
        self, golden_engine: Engine
    ):
        """A boat with too few races counts in n_boats_total only."""
        with golden_engine.begin() as conn:
            _seed_boat(conn, 303, "NEWCOMER", "GBR303", "J/99")
            # One race only — well under the 5-race threshold.
            _seed_result(
                conn, 303, "Solo", None, _race_day(0),
                place=1, fleet=5, rating=1.010,
            )
        baseline = class_baseline_v1(golden_engine, "J/99")
        assert baseline.n_boats == 2  # only HELD + CHASER qualify
        assert baseline.n_boats_total == 3
        assert baseline.mean_rai == pytest.approx(0.0, abs=1e-9)

    def test_empty_class_baseline(self, golden_engine: Engine):
        baseline = class_baseline_v1(golden_engine, "No/SuchDesign")
        assert baseline.n_boats == 0
        assert baseline.n_boats_total == 0
        assert baseline.mean_rai is None

    def test_baseline_pure_layer_matches_bridge(self, golden_engine: Engine):
        held = compute_rai_v1(golden_engine, HELD_ID)
        chaser = compute_rai_v1(golden_engine, CHASER_ID)
        pure = class_baseline_from_results("J/99", [held, chaser])
        bridge = class_baseline_v1(golden_engine, "J/99")
        assert pure.mean_rai == pytest.approx(bridge.mean_rai, abs=1e-9)
        assert pure.n_boats == bridge.n_boats


# ---------------------------------------------------------------------------
# 3. Condition splits by TWS band
# ---------------------------------------------------------------------------


class TestTwsSplits:
    def test_splits_pinned_for_golden_fixture(self, golden_engine: Engine):
        res = compute_rai_v1(golden_engine, HELD_ID)
        splits = {s.band: s for s in res.condition_splits}
        assert res.n_wind_observed == N_RACES
        # 4 light races (all advantage 0.0) and 4 fresh races (all 0.0).
        assert splits["light"].status == STATUS_OK
        assert splits["light"].n_races == 4
        assert splits["light"].rai == pytest.approx(0.0, abs=1e-9)
        assert splits["fresh"].status == STATUS_OK
        assert splits["fresh"].n_races == 4
        assert splits["fresh"].rai == pytest.approx(0.0, abs=1e-9)
        # No wind readings in medium/heavy bands.
        assert splits["medium"].status == STATUS_INSUFFICIENT
        assert splits["medium"].n_races == 0
        assert splits["medium"].rai is None
        assert splits["heavy"].status == STATUS_INSUFFICIENT

    def test_band_below_min_band_races_is_insufficient(self):
        obs = [
            RaceObservation(
                boat_id=1, event_name=f"E{i}", race_name=None,
                event_date=f"2024-01-{i + 1:02d}", place=1, fleet_size=4,
                rating_value=1.0,
                raw={"tws": 6.0} if i == 0 else None,
            )
            for i in range(5)
        ]
        fields = {
            (f"E{i}", None, f"2024-01-{i + 1:02d}"): (1.0, 1.01, 1.02, 1.03)
            for i in range(5)
        }
        res = compute_rai_from_observations(obs, fields, info=BoatInfo(boat_id=1))
        light = next(s for s in res.condition_splits if s.band == "light")
        assert light.n_races == 1
        assert light.status == STATUS_INSUFFICIENT
        assert light.rai is None
        assert res.n_wind_observed == 1

    def test_no_wind_data_means_no_splits_but_rai_still_computed(self):
        obs = [
            RaceObservation(
                boat_id=1, event_name=f"E{i}", race_name=None,
                event_date=f"2024-02-{i + 1:02d}", place=1, fleet_size=3,
                rating_value=1.0, raw=None,
            )
            for i in range(5)
        ]
        fields = {
            (f"E{i}", None, f"2024-02-{i + 1:02d}"): (1.0, 1.01, 1.02)
            for i in range(5)
        }
        res = compute_rai_from_observations(obs, fields, info=BoatInfo(boat_id=1))
        assert res.status == STATUS_OK
        assert res.n_wind_observed == 0
        assert all(s.status == STATUS_INSUFFICIENT for s in res.condition_splits)
        assert all(s.n_races == 0 for s in res.condition_splits)

    def test_extract_tws_variants_and_absence(self):
        assert extract_tws({"tws": 12.5}) == 12.5
        assert extract_tws({"wind_speed": "10 kt"}) == 10.0
        assert extract_tws({"true_wind_speed": "9"}) == 9.0
        assert extract_tws({"tws": 0}) is None  # ≤ 0 treated as noise
        assert extract_tws({"tws": 99}) is None  # > 60 kn discarded
        assert extract_tws({"unrelated": 5}) is None
        assert extract_tws(None) is None

    def test_tws_band_edges(self):
        assert tws_band_for(0.5) == "light"
        assert tws_band_for(7.99) == "light"
        assert tws_band_for(8.0) == "medium"  # [8, 14)
        assert tws_band_for(13.99) == "medium"
        assert tws_band_for(14.0) == "fresh"
        assert tws_band_for(19.99) == "fresh"
        assert tws_band_for(20.0) == "heavy"
        assert tws_band_for(35.0) == "heavy"
        assert tws_band_for(None) is None


# ---------------------------------------------------------------------------
# 4. Minimum-race threshold enforcement
# ---------------------------------------------------------------------------


class TestMinRaceThreshold:
    @pytest.mark.parametrize("n_races", [0, 1, 4])
    def test_below_threshold_returns_insufficient(self, n_races: int):
        obs = [
            RaceObservation(
                boat_id=7, event_name=f"E{i}", race_name=None,
                event_date=f"2024-03-{i + 1:02d}", place=1, fleet_size=2,
                rating_value=1.0,
            )
            for i in range(n_races)
        ]
        fields = {
            (f"E{i}", None, f"2024-03-{i + 1:02d}"): (1.0, 1.05)
            for i in range(n_races)
        }
        res = compute_rai_from_observations(obs, fields, info=BoatInfo(boat_id=7))
        assert res.status == STATUS_INSUFFICIENT
        assert res.meets_min_races is False
        assert res.rai is None
        assert res.ci_lower is None and res.ci_upper is None
        assert res.n_races == n_races
        assert res.min_races_required == DEFAULT_CONFIG.min_races
        assert "Insufficient data" in res.interpretation

    def test_exactly_at_threshold_is_ok(self):
        obs = [
            RaceObservation(
                boat_id=7, event_name=f"E{i}", race_name=None,
                event_date=f"2024-04-{i + 1:02d}", place=1, fleet_size=2,
                rating_value=1.0,
            )
            for i in range(DEFAULT_CONFIG.min_races)
        ]
        fields = {
            (f"E{i}", None, f"2024-04-{i + 1:02d}"): (1.0, 1.05)
            for i in range(DEFAULT_CONFIG.min_races)
        }
        res = compute_rai_from_observations(obs, fields, info=BoatInfo(boat_id=7))
        assert res.status == STATUS_OK
        assert res.meets_min_races is True
        assert res.rai is not None

    def test_threshold_is_configurable(self):
        config = RAIRulesetConfigV1(min_races=3)
        obs = [
            RaceObservation(
                boat_id=7, event_name=f"E{i}", race_name=None,
                event_date=f"2024-05-{i + 1:02d}", place=1, fleet_size=2,
                rating_value=1.0,
            )
            for i in range(3)
        ]
        fields = {
            (f"E{i}", None, f"2024-05-{i + 1:02d}"): (1.0, 1.05)
            for i in range(3)
        }
        res = compute_rai_from_observations(
            obs, fields, info=BoatInfo(boat_id=7), config=config
        )
        assert res.status == STATUS_OK
        assert res.min_races_required == 3

    def test_unknown_boat_is_insufficient(self, golden_engine: Engine):
        res = compute_rai_v1(golden_engine, 99999)
        assert res.status == STATUS_INSUFFICIENT
        assert res.n_races == 0
        assert res.rai is None


# ---------------------------------------------------------------------------
# 5. Reproducibility per dataset version
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_identical_rerun_is_bit_identical(self, golden_engine: Engine):
        first = compute_rai_v1(golden_engine, HELD_ID).to_dict()
        second = compute_rai_v1(golden_engine, HELD_ID).to_dict()
        assert first == second
        # Serialisation is stable too (this is what gets cached/published).
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_pure_layer_reproducible(self):
        obs = _held_observations()
        fields = _golden_fields()
        a = compute_rai_from_observations(
            obs, fields, info=BoatInfo(boat_id=HELD_ID)
        ).to_dict()
        b = compute_rai_from_observations(
            obs, fields, info=BoatInfo(boat_id=HELD_ID)
        ).to_dict()
        assert a == b

    def test_dataset_fingerprint_changes_with_data(self):
        obs = _held_observations()
        fp_a = dataset_fingerprint(tuple(obs))
        assert dataset_fingerprint(tuple(obs)) == fp_a  # stable

        # Row added ⇒ different fingerprint (different dataset version).
        extra = obs + [
            RaceObservation(
                boat_id=HELD_ID, event_name="Extra", race_name=None,
                event_date="2024-12-01", place=1, fleet_size=2,
                rating_value=HELD_TCC,
            )
        ]
        assert dataset_fingerprint(tuple(extra)) != fp_a

        # Row mutated ⇒ different fingerprint.
        mutated = list(obs)
        m = mutated[0]
        mutated[0] = RaceObservation(
            boat_id=m.boat_id, event_name=m.event_name, race_name=m.race_name,
            event_date=m.event_date, place=2, fleet_size=m.fleet_size,
            rating_value=m.rating_value, raw=m.raw,
        )
        assert dataset_fingerprint(tuple(mutated)) != fp_a

    def test_bridge_matches_pure_fingerprint(self, golden_engine: Engine):
        bridged = compute_rai_v1(golden_engine, HELD_ID)
        pure = compute_rai_from_observations(
            _held_observations(), _golden_fields(),
            info=BoatInfo(boat_id=HELD_ID, boat_name="HELD",
                          sail_number="GBR101", design="J/99"),
        )
        assert bridged.dataset_fingerprint == pure.dataset_fingerprint
        assert bridged.rai == pytest.approx(pure.rai, abs=1e-9)

    def test_config_fingerprint_changes_with_config(self):
        assert DEFAULT_CONFIG.schema == RAI_CONFIG_SCHEMA
        other = RAIRulesetConfigV1(min_races=3)
        assert other.fingerprint() != DEFAULT_CONFIG.fingerprint()
        assert RAIRulesetConfigV1().fingerprint() == DEFAULT_CONFIG.fingerprint()

    def test_result_carries_both_fingerprints(self, golden_engine: Engine):
        res = compute_rai_v1(golden_engine, HELD_ID)
        assert res.dataset_fingerprint
        assert res.config_fingerprint == DEFAULT_CONFIG.fingerprint()


# ---------------------------------------------------------------------------
# 6. Sensitivity to identity-merge errors
# ---------------------------------------------------------------------------


class TestIdentityMergeSensitivity:
    """If an identity merge wrongly absorbs another boat's results, the RAI
    must move — the index is computed off resolved identities, so merge
    quality shows up directly in the numbers."""

    def test_merge_error_shifts_rai_by_pinned_amount(self, golden_engine: Engine):
        clean = compute_rai_v1(golden_engine, HELD_ID)
        assert clean.rai == pytest.approx(0.0, abs=1e-9)

        # Pollute HELD's identity with CHASER's 8 second places (each −50).
        with golden_engine.begin() as conn:
            _seed_merge_error(conn)
        polluted = compute_rai_v1(golden_engine, HELD_ID)

        assert polluted.n_scored == 2 * N_RACES
        # mean of 8 × 0.0 (HELD's own wins) and 8 × −50.0 (absorbed lasts)
        assert polluted.rai == pytest.approx(-25.0, abs=1e-9)
        # The dataset version changes with the polluted identity graph.
        assert polluted.dataset_fingerprint != clean.dataset_fingerprint
        # With variance now non-zero, the CI is a real interval.
        assert polluted.ci_lower < polluted.rai < polluted.ci_upper

    def test_split_restores_original_rai(self, golden_engine: Engine):
        """Pollution removed (identity split) ⇒ RAI returns to the golden value."""
        clean_fp = compute_rai_v1(golden_engine, HELD_ID).dataset_fingerprint
        with golden_engine.begin() as conn:
            _seed_merge_error(conn)
        polluted = compute_rai_v1(golden_engine, HELD_ID)
        assert polluted.rai == pytest.approx(-25.0, abs=1e-9)

        with golden_engine.begin() as conn:
            # Split: delete the wrongly-merged second places keyed to HELD.
            conn.execute(text(
                "DELETE FROM race_results WHERE boat_id = :bid AND place = 2"
            ), {"bid": HELD_ID})
        restored = compute_rai_v1(golden_engine, HELD_ID)
        assert restored.n_scored == N_RACES
        assert restored.rai == pytest.approx(0.0, abs=1e-9)
        # Restoring the identity graph restores the dataset fingerprint.
        assert restored.dataset_fingerprint == clean_fp


# ---------------------------------------------------------------------------
# 7. Analytics filter behaviour (corrected results only)
# ---------------------------------------------------------------------------


class TestAnalyticsFilter:
    def test_unfinished_and_unrated_results_excluded(self, golden_engine: Engine):
        with golden_engine.begin() as conn:
            # DNF result — must not enter the computation.
            _seed_result(
                conn, HELD_ID, "DNFEvent", None, _race_day(40),
                place=None, fleet=2, rating=HELD_TCC, status="dnf",
            )
            # Twilight race — excluded by the shared analytics filter.
            _seed_result(
                conn, HELD_ID, "Twilight Series", None, _race_day(41),
                place=1, fleet=2, rating=HELD_TCC,
            )
            # No rating — excluded.
            conn.execute(text(
                "INSERT INTO race_results (boat_id, event_name, event_date,"
                " place, fleet_size, status, rating_value)"
                " VALUES (:bid, 'NoRating', '2024-09-01', 1, 2, 'finished', NULL)"
            ), {"bid": HELD_ID})
        res = compute_rai_v1(golden_engine, HELD_ID)
        assert res.n_races == N_RACES  # unchanged
        assert res.rai == pytest.approx(0.0, abs=1e-9)

    def test_expected_percentile_math(self):
        # IRC: lower TCC is owed time ⇒ expected to win; higher expects last.
        assert expected_percentile((1.0, 1.1), 1.0) == pytest.approx(0.5)
        assert expected_percentile((1.0, 1.1), 1.1) == pytest.approx(1.0)
        assert expected_percentile((1.0, 1.05, 1.1), 1.05) == pytest.approx(2 / 3)
        # Single-boat field carries no information.
        assert expected_percentile((1.0,), 1.0) is None
