"""SM-01-08 — the CI regression gate.

``test_model_change_blocks_gate`` proves the gate actually blocks: a
simulated model regression (golden bundle tampered as if a coefficient
moved) makes the comparison fail. ``test_golden_artifacts_checked_in``
guards against the fixtures themselves going missing.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.model_regression

from irc_data.analysis.backtest import (
    GOLDEN_BOATS,
    compare_bundles,
    golden_bundle_path,
    golden_dataset_path,
)


def test_golden_artifacts_checked_in():
    for fb in GOLDEN_BOATS:
        ds = golden_dataset_path(fb.slug)
        gb = golden_bundle_path(fb.slug)
        assert ds.exists(), f"missing fixture dataset {ds}"
        assert gb.exists(), f"missing golden bundle {gb}"

        dataset = json.loads(ds.read_text())
        assert dataset["counts"]["boats"] > 1
        assert dataset["counts"]["race_results"] > 100

        bundle = json.loads(gb.read_text())
        assert bundle["schema_version"] == "ReportFactsV1"
        assert bundle["boat"]["name"] == fb.boat_name
        assert bundle["boat"]["design"] == fb.design
        assert bundle["fixture"]["slug"] == fb.slug


def test_model_change_blocks_gate():
    """Simulate a model regression: perturb the golden file the way a real
    model change would (a coefficient moves, a count changes, a section
    disappears) and assert the comparator rejects it."""
    golden = json.loads(golden_bundle_path(GOLDEN_BOATS[0].slug).read_text())
    golden.pop("fixture", None)

    # 1. A coefficient moving by > tolerance must be caught.
    tampered = json.loads(json.dumps(golden))
    coef_path = tampered["sections"]["s03_rating_anatomy"]["decomposition"]
    if coef_path:
        coef_path[0]["beta"] = float(coef_path[0]["beta"]) * 1.5 + 0.01
        diffs = compare_bundles(golden, tampered)
        assert any("beta" in d.path for d in diffs)

    # 2. A headline count changing must be caught.
    tampered = json.loads(json.dumps(golden))
    tampered["sections"]["s06_performance"]["finishes"] += 1
    diffs = compare_bundles(golden, tampered)
    assert any(d.path == "sections.s06_performance.finishes" for d in diffs)

    # 3. Dropping a section entirely must be caught.
    tampered = json.loads(json.dumps(golden))
    del tampered["sections"]["s10_rivals"]
    diffs = compare_bundles(golden, tampered)
    assert any(d.path.startswith("sections.s10_rivals") for d in diffs)

    # 4. Sanity: the untouched file compares clean against itself.
    assert compare_bundles(golden, json.loads(json.dumps(golden))) == []
