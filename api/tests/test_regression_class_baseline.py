"""Verify get_boat_sensitivity_context returns class baseline TCC stats.

The substantial report needs to anchor decomposition with "median Sunfast
3300 rates X; this boat rates Y; here's where the Y−X delta came from."
The regression engine already returns per-measurement coefficients and
this boat's value; it does not currently return the class TCC baseline.
"""
from irc_data.db.connection import get_engine
from irc_data.analysis.regression import get_boat_sensitivity_context


def test_class_baseline_tcc_present_for_sunfast_3300():
    eng = get_engine()
    # SUN FISH (id=12330) is a Sunfast 3300 we know has Tier A data.
    r = get_boat_sensitivity_context(eng, 12330, "Sunfast 3300")
    assert r is not None
    assert "class_baseline" in r
    cb = r["class_baseline"]
    assert "mean_tcc" in cb and 0.5 < cb["mean_tcc"] < 1.5
    assert "median_tcc" in cb and 0.5 < cb["median_tcc"] < 1.5
    assert "p25_tcc" in cb and "p75_tcc" in cb
    assert cb["p25_tcc"] < cb["median_tcc"] < cb["p75_tcc"]
    assert "this_boat_tcc" in cb
    assert "this_boat_percentile" in cb
    assert 0 <= cb["this_boat_percentile"] <= 100
