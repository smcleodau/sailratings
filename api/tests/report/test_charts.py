"""Verify each chart factory function returns a valid PNG payload.

Charts are produced inline (no temp files), base64-inlined into the
HTML template by the orchestrator. Each function takes the relevant
Facts dataclass and returns PNG bytes.
"""
from decimal import Decimal
from datetime import date

from irc_data.api.services.report.charts import (
    render_anatomy_bar, render_tcc_timeseries,
    render_class_distribution, render_sensitivity_bar,
    render_results_timeline,
)
from irc_data.api.services.report.facts import (
    RatingAnatomyFacts, RatingEvolutionFacts, ClassContextFacts,
    SensitivityFacts, PerformanceFacts,
    MeasurementContribution, RatingSnapshot, RaceResultLite,
)


def _is_png(b: bytes) -> bool:
    return b[:8] == b"\x89PNG\r\n\x1a\n"


def test_anatomy_bar_returns_png():
    facts = RatingAnatomyFacts(
        boat_name="SUN FISH",
        tcc_now=Decimal("1.025"), class_mean_tcc=1.0025,
        class_median_tcc=1.002, explained_variance_pct=93.4,
        model_tier="A", n_boats_in_class=82,
        decomposition=[
            MeasurementContribution("displacement", 3696, 3981, -285, 0.0096, "per 100kg", -0.003373),
            MeasurementContribution("muw", 1.45, 1.08, 0.37, 0.0042, "per 0.1m", 0.001124),
            MeasurementContribution("hlu", 11.09, 12.12, -1.03, -0.0030, "per 0.1m", 0.000291),
            MeasurementContribution("spinnakers", 2, 2.82, -0.82, -0.0021, "per sail", 0.002505),
        ],
    )
    png = render_anatomy_bar(facts)
    assert _is_png(png)
    assert len(png) > 2000  # not an empty figure


def test_tcc_timeseries_returns_png():
    facts = RatingEvolutionFacts(
        boat_name="SUN FISH",
        snapshots=[
            RatingSnapshot(date(2023, 6, 1), Decimal("1.018"), 2023, "irc_tcc"),
            RatingSnapshot(date(2024, 6, 1), Decimal("1.022"), 2024, "irc_tcc"),
            RatingSnapshot(date(2025, 6, 1), Decimal("1.025"), 2025, "irc_tcc"),
        ],
        first_snapshot_tcc=Decimal("1.018"),
        latest_snapshot_tcc=Decimal("1.025"),
        total_movement=0.007,
    )
    png = render_tcc_timeseries(facts)
    assert _is_png(png)


def test_class_distribution_returns_png():
    facts = ClassContextFacts(
        design="Sunfast 3300", class_n=82,
        class_tcc_min=0.891, class_tcc_max=1.078,
        class_tcc_median=1.002, class_tcc_mean=1.0025,
        this_boat_tcc=1.025, this_boat_percentile=78.0,
    )
    # Renderer also needs the per-boat TCCs — we pass them separately.
    tcc_list = [0.891 + i * 0.002 for i in range(94)]
    png = render_class_distribution(facts, tcc_list)
    assert _is_png(png)


def test_sensitivity_bar_returns_png():
    facts = SensitivityFacts(
        design="Sunfast 3300", model_tier="A", n_boats_in_class=82,
        r_squared=0.934,
        coefficients=[
            MeasurementContribution("displacement", 3696, 3981, -285, 0.0096, "per 100kg", -0.003373),
            MeasurementContribution("muw", 1.45, 1.08, 0.37, 0.0042, "per 0.1m", 0.001124),
            MeasurementContribution("hlu", 11.09, 12.12, -1.03, -0.0030, "per 0.1m", 0.000291),
        ],
    )
    png = render_sensitivity_bar(facts)
    assert _is_png(png)


def test_results_timeline_returns_png():
    facts = PerformanceFacts(
        boat_name="SUN FISH", finishes=31, wins=3, podiums=13,
        distinct_events=61, rai_percentile=58.0, rai_interpretation=None,
        recent_results=[
            RaceResultLite(date(2025, 11, 23), "Race A", "R1", 5, 8, "Div 1", "finished"),
            RaceResultLite(date(2025, 11, 30), "Race B", "R1", 2, 8, "Div 1", "finished"),
            RaceResultLite(date(2025, 12, 7),  "Race C", "R1", 8, 10, "Div 1", "finished"),
        ],
    )
    png = render_results_timeline(facts)
    assert _is_png(png)
