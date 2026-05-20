"""Verify facts_builders.build_rating_anatomy populates a
RatingAnatomyFacts that points at real DB values for SUN FISH."""
from irc_data.db.connection import get_engine
from irc_data.api.services.report.facts import RatingAnatomyFacts
from irc_data.api.services.report.facts_builders import build_rating_anatomy

from tests.report.fixtures.sun_fish_facts import (
    SUN_FISH_BOAT_ID, SUN_FISH_DESIGN, SUN_FISH_TCC_LOWER, SUN_FISH_TCC_UPPER,
)


def test_build_rating_anatomy_for_sun_fish():
    eng = get_engine()
    facts = build_rating_anatomy(eng, SUN_FISH_BOAT_ID)
    assert isinstance(facts, RatingAnatomyFacts)
    assert facts.boat_name.upper() == "SUN FISH"
    assert SUN_FISH_TCC_LOWER < float(facts.tcc_now) < SUN_FISH_TCC_UPPER
    assert facts.class_median_tcc is not None
    assert 0.95 < facts.class_median_tcc < 1.05
    assert facts.n_boats_in_class >= 50
    assert facts.model_tier in ("A", "B", "C")
    # Decomposition should have something — at least 5 features for any tier.
    assert len(facts.decomposition) >= 5
    # Find displacement and check the contribution direction matches
    # our known fact (lighter than median, lower TCC penalty therefore positive contrib).
    disp = next((c for c in facts.decomposition if c.field == "displacement"), None)
    assert disp is not None
    assert disp.this_boat < disp.class_mean  # she's lighter
    # The signed contribution should be small (< 0.05 TCC absolute).
    assert abs(disp.contrib_tcc) < 0.05


def test_build_rating_anatomy_handles_unknown_boat_gracefully():
    eng = get_engine()
    facts = build_rating_anatomy(eng, boat_id=999_999_999)
    # Returns a Facts object with empty/None fields rather than raising.
    assert isinstance(facts, RatingAnatomyFacts)
    assert facts.decomposition == []
    assert facts.n_boats_in_class == 0
