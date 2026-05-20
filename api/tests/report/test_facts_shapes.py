"""Pin the public shape of every Facts dataclass.

Facts dataclasses are the truth-discipline contract: each section's
Claude prompt sees ONLY the fields on the Facts object, and the prompt
forbids inventing numbers not in those fields. If the shape of Facts
ever changes, the prompt template must be updated in lockstep. These
tests force that conversation.
"""
from dataclasses import fields
from decimal import Decimal
from datetime import date

from irc_data.api.services.report.facts import (
    ExecutiveSummaryFacts, IdentityFacts, RatingAnatomyFacts,
    RatingEvolutionFacts, ClassContextFacts, PerformanceFacts,
    SensitivityFacts, OptimisationFacts, FormulaDriftFacts,
    RivalsFacts, AppendixFacts, MeasurementContribution, RatingSnapshot,
    RivalSummary,
)


def test_rating_anatomy_facts_has_required_fields():
    fs = {f.name for f in fields(RatingAnatomyFacts)}
    assert fs >= {"boat_name", "tcc_now", "class_mean_tcc", "class_median_tcc",
                  "decomposition", "explained_variance_pct", "model_tier",
                  "n_boats_in_class"}


def test_measurement_contribution_dataclass():
    mc = MeasurementContribution(
        field="displacement", this_boat=3696.0, class_mean=3981.0,
        delta=-285.0, contrib_tcc=0.0096, unit="per 100kg",
        beta=-0.003373,
    )
    assert mc.contrib_tcc == 0.0096
    assert mc.unit == "per 100kg"


def test_rating_snapshot_dataclass():
    s = RatingSnapshot(date=date(2025, 8, 14), tcc=Decimal("1.025"),
                      cert_year=2025, source="irc_tcc")
    assert s.tcc == Decimal("1.025")


def test_performance_facts_has_required_fields():
    fs = {f.name for f in fields(PerformanceFacts)}
    assert fs >= {"finishes", "wins", "podiums", "distinct_events",
                  "rai_percentile", "recent_results", "head_to_head"}


def test_rivals_facts_uses_rival_summary():
    rs = RivalSummary(boat_id=1, name="Foo", sail_number="GBR1R",
                      country="GBR", tcc=Decimal("1.020"),
                      recent_finishes_count=12,
                      head_to_head_wins=3, head_to_head_losses=5)
    assert rs.tcc == Decimal("1.020")
