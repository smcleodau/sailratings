"""Tests for DP-04-03 — explainable pairwise match scoring.

Covers:

* the deterministic feature ruleset (each feature individually),
* the scored-pair contract (score = Σ weight·value, missingness preserved,
  per-feature contributions non-negative and bounded),
* reproducibility (same inputs → identical score + explanation),
* per-entity-type threshold calibration on labelled examples,
* the uncertain band routing to adjudication (DP-04-05 integration),
* holdout evaluation reporting precision, recall, calibration and
  high-cost false merges.
"""

from __future__ import annotations

from datetime import date

import pytest

from irc_data.matching.blocking import (
    CandidateGenerator,
    CandidatePair,
    EntityObservation,
)
from irc_data.matching.scoring import (
    DEFAULT_THRESHOLDS_V1,
    KNOWN_SCORER_RULESETS,
    SCORER_RULESET_V1,
    SCORER_RULESET_V1_ID,
    SCHEMA_VERSION,
    THRESHOLD_CONFIG_V1_ID,
    LabelledPair,
    PairwiseScorer,
    ScoredPairV1,
    ScoringConfig,
    ScoringError,
    ScoringFeature,
    ThresholdConfig,
    UnknownEntityTypeError,
    UnknownScorerRulesetError,
    evaluate_holdout,
    fit_thresholds,
    get_scorer_ruleset,
    get_thresholds,
    split_labelled,
)


def obs(obs_id: str, **kwargs) -> EntityObservation:
    return EntityObservation(observation_id=obs_id, **kwargs)


def _pair(a: str, b: str, rules=("R01",), keys=("R01:K",)) -> CandidatePair:
    return CandidatePair(left_id=a, right_id=b, rules_fired=tuple(rules),
                         matching_keys=tuple(keys))


def _score(left, right, rules=("R01",), **scorer_kw):
    scorer = PairwiseScorer()
    return scorer.score_pair(_pair(left.observation_id, right.observation_id, rules),
                             left, right, **scorer_kw)


# ---------------------------------------------------------------------------
# Feature ruleset
# ---------------------------------------------------------------------------


class TestFeatureRuleset:
    def test_ruleset_is_versioned_and_fetchable(self):
        assert get_scorer_ruleset(SCORER_RULESET_V1_ID) is SCORER_RULESET_V1
        assert SCORER_RULESET_V1_ID in KNOWN_SCORER_RULESETS

    def test_unknown_ruleset_rejected(self):
        with pytest.raises(UnknownScorerRulesetError):
            get_scorer_ruleset("scorer-rules-v99")

    def test_weights_sum_to_one(self):
        assert abs(sum(f.weight for f in SCORER_RULESET_V1) - 1.0) < 1e-9

    def test_feature_ids_unique(self):
        ids = [f.feature_id for f in SCORER_RULESET_V1]
        assert len(ids) == len(set(ids))

    def test_feature_weight_must_be_positive(self):
        with pytest.raises(ScoringError):
            ScoringFeature("FX", "bad", 0.0, "d", fn=lambda a, b, p: 0.0)


class TestIndividualFeatures:
    def _val(self, name, left, right, rules=("R01",)):
        sp = _score(left, right, rules)
        return {c.name: c for c in sp.feature_contributions}[name]

    def test_sail_exact_shared_token(self):
        c = self._val("sail_exact",
                      obs("a", sail_number="AUS 4343"),
                      obs("b", sail_number="EAUS4343"))
        assert c.value == 1.0 and not c.missing

    def test_sail_exact_short_bare_numeric_excluded(self):
        # "12" is a bare short numeric — ambiguous across countries.
        c = self._val("sail_exact", obs("a", sail_number="12"), obs("b", sail_number="12"))
        assert c.missing

    def test_sail_exact_missing(self):
        c = self._val("sail_exact", obs("a"), obs("b", sail_number="AUS 1"))
        assert c.missing

    def test_registry_exact(self):
        c = self._val("registry_exact",
                      obs("a", registry_id="AUSYC12345"),
                      obs("b", registry_id="ausyc12345"))
        assert c.value == 1.0

    def test_name_similarity_identical(self):
        c = self._val("name_similarity",
                      obs("a", name="Black Jack"), obs("b", name="BLACK  JACK"))
        assert c.value == 1.0

    def test_name_similarity_partial(self):
        c = self._val("name_similarity",
                      obs("a", name="Ragamuffin"), obs("b", name="Raggamuffin"))
        assert 0.0 < c.value < 1.0

    def test_design_exact_punctuation_collapsed(self):
        c = self._val("design_exact",
                      obs("a", design="J/122"), obs("b", design="J122"))
        assert c.value == 1.0

    def test_country_match(self):
        c = self._val("country_match",
                      obs("a", country="AUS"), obs("b", country="aus"))
        assert c.value == 1.0

    def test_loa_closeness_tapers(self):
        near = self._val("loa_closeness", obs("a", loa_m=12.0), obs("b", loa_m=12.1))
        far = self._val("loa_closeness", obs("a", loa_m=12.0), obs("b", loa_m=20.0))
        assert 0.9 < near.value <= 1.0
        assert far.value == 0.0

    def test_year_closeness_tapers(self):
        near = self._val("year_closeness",
                         obs("a", year_built=2008), obs("b", year_built=2009))
        far = self._val("year_closeness",
                        obs("a", year_built=2000), obs("b", year_built=2015))
        assert 0.0 < near.value <= 1.0
        assert far.value == 0.0

    def test_temporal_overlap(self):
        # 2008 build vs a certificate valid 2007→2009: point eras overlap.
        yes = self._val("temporal_overlap",
                        obs("a", year_built=2008),
                        obs("b", valid_from=date(2007, 1, 1), valid_to=date(2009, 3, 1)))
        # 1980 build vs a 2010→2011 validity window: clearly disjoint.
        no = self._val("temporal_overlap",
                       obs("a", year_built=1980),
                       obs("b", valid_from=date(2010, 1, 1), valid_to=date(2011, 1, 1)))
        assert yes.value == 1.0
        assert no.value == 0.0

    def test_blocking_corroboration_by_rule_count(self):
        one = self._val("blocking_corroboration",
                        obs("a", sail_number="AUS 4343"),
                        obs("b", sail_number="AUS4343"), rules=("R01",))
        two = self._val("blocking_corroboration",
                        obs("a", sail_number="AUS 4343"),
                        obs("b", sail_number="AUS4343"), rules=("R01", "R05"))
        three = self._val("blocking_corroboration",
                          obs("a", sail_number="AUS 4343"),
                          obs("b", sail_number="AUS4343"),
                          rules=("R01", "R03", "R05"))
        assert one.value == 0.0
        assert two.value == 0.5
        assert three.value == 1.0


# ---------------------------------------------------------------------------
# Score construction & explainability contract
# ---------------------------------------------------------------------------


class TestScoreConstruction:
    def test_score_equals_sum_of_points(self):
        sp = _score(obs("a", sail_number="AUS 4343", name="Wild Oats XI",
                        design="Reichel Pugh 100", country="AUS", loa_m=30.5,
                        year_built=2005),
                    obs("b", sail_number="EAUS4343", name="WILD OATS XI",
                        design="Reichel Pugh 100", country="AUS", loa_m=30.48,
                        year_built=2005),
                    rules=("R01", "R05"))
        total = sum(c.points for c in sp.feature_contributions)
        assert abs(sp.deterministic_score - total) < 1e-9

    def test_score_bounded_in_unit_interval(self):
        # Everything agreeing should approach the cap, never exceed 1.
        sp = _score(obs("a", sail_number="AUS 4343", registry_id="R1",
                        name="X", design="D", country="AUS", loa_m=10.0,
                        beam_m=3.5, year_built=2001,
                        valid_from=date(2001, 1, 1), valid_to=date(2002, 1, 1)),
                    obs("b", sail_number="AUS 4343", registry_id="R1",
                        name="X", design="D", country="AUS", loa_m=10.0,
                        beam_m=3.5, year_built=2001,
                        valid_from=date(2001, 6, 1), valid_to=date(2003, 1, 1)),
                    rules=("R01", "R02", "R05"))
        assert 0.0 <= sp.score <= 1.0
        assert 0.0 <= sp.deterministic_score <= 1.0

    def test_missingness_preserved_and_never_negative(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"))
        assert sp.missing_features  # many features have no evidence
        for c in sp.feature_contributions:
            assert c.points >= 0.0
            if c.missing:
                assert c.value is None and c.points == 0.0
        # absent evidence can never raise the score above the sail-only mass
        assert abs(sp.score - 0.22) < 1e-9

    def test_explanation_line_items_present(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"))
        text = "\n".join(sp.explanation)
        assert "sail_exact" in text
        assert "MISSING" in text  # missingness is surfaced, not hidden

    def test_contribution_vector_complete(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"))
        assert len(sp.feature_contributions) == len(SCORER_RULESET_V1)
        names = {c.name for c in sp.feature_contributions}
        assert names == {f.name for f in SCORER_RULESET_V1}


class TestReproducibility:
    def test_same_inputs_same_score_and_explanation(self):
        left = obs("a", sail_number="AUS 4343", name="Wild Oats XI",
                   design="RP100", country="AUS", loa_m=30.5, year_built=2005)
        right = obs("b", sail_number="EAUS4343", name="WILD OATS XI",
                    design="RP100", country="AUS", loa_m=30.48, year_built=2005)
        pair = _pair("a", "b", ("R01", "R05"))
        scorer = PairwiseScorer()
        first = scorer.score_pair(pair, left, right)
        second = scorer.score_pair(pair, left, right)
        assert first.score == second.score
        assert first.explanation == second.explanation
        assert first.to_dict() == second.to_dict()

    def test_batch_scoring_is_deterministic(self):
        observations = [
            obs("a", sail_number="AUS 4343", name="Wild Oats XI"),
            obs("b", sail_number="EAUS4343"),
            obs("c", sail_number="GBR 1", name="X", design="D1", country="GBR"),
            obs("d", sail_number="GBR 2", name="Y", design="D2", country="GBR"),
        ]
        report = CandidateGenerator().generate(observations)
        scorer = PairwiseScorer()
        one = scorer.score(observations, report)
        two = scorer.score(observations, report)
        assert [s.score for s in one.scored_pairs] == [s.score for s in two.scored_pairs]
        assert one.config_fingerprint == two.config_fingerprint

    def test_config_fingerprint_changes_with_config(self):
        base = ScoringConfig().fingerprint()
        other = ScoringConfig(model_weight=0.2).fingerprint()
        assert base != other


# ---------------------------------------------------------------------------
# Optional model-score blending
# ---------------------------------------------------------------------------


class TestModelBlend:
    def test_default_is_fully_deterministic(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"),
                    model_score=0.99)
        # model_weight defaults to 0 → model_score is recorded but unused
        assert sp.model_weight == 0.0
        assert abs(sp.score - sp.deterministic_score) < 1e-9

    def test_blend_moves_score_toward_model(self):
        left = obs("a", sail_number="AUS 4343")
        right = obs("b", sail_number="AUS4343")
        pair = _pair("a", "b")
        det = PairwiseScorer().score_pair(pair, left, right)
        blended = PairwiseScorer(ScoringConfig(model_weight=0.4)).score_pair(
            pair, left, right, model_score=0.9
        )
        expected = 0.6 * det.deterministic_score + 0.4 * 0.9
        assert abs(blended.score - expected) < 1e-9
        assert blended.model_score == 0.9 and blended.model_weight == 0.4

    def test_model_weight_capped(self):
        with pytest.raises(ScoringError):
            ScoringConfig(model_weight=0.9)

    def test_model_score_validated(self):
        scorer = PairwiseScorer()
        with pytest.raises(ScoringError):
            scorer.score_pair(_pair("a", "b"), obs("a"), obs("b"), model_score=1.5)


# ---------------------------------------------------------------------------
# Threshold config & per-entity-type calibration
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_default_thresholds_shipped_for_boat(self):
        t = get_thresholds("boat")
        assert t.entity_type == "boat"
        assert 0.0 <= t.auto_reject_below < t.auto_merge_at_or_above <= 1.0
        assert t.config_id == THRESHOLD_CONFIG_V1_ID

    def test_unknown_entity_type_rejected(self):
        with pytest.raises(UnknownEntityTypeError):
            get_thresholds("airplane")

    def test_invalid_band_rejected(self):
        with pytest.raises(ScoringError):
            ThresholdConfig(auto_reject_below=0.9, auto_merge_at_or_above=0.2)

    def test_threshold_round_trip(self):
        t = ThresholdConfig(entity_type="boat", auto_reject_below=0.15,
                            auto_merge_at_or_above=0.88, fit_pairs=42,
                            fit_fingerprint="abc123")
        assert ThresholdConfig.from_dict(t.to_dict()) == t


def _mk_scored(score: float, obs_ids=("a", "b")) -> ScoredPairV1:
    """Build a minimal ScoredPairV1 with a forced score (for calibration tests)."""
    pair = _pair(obs_ids[0], obs_ids[1])
    t = DEFAULT_THRESHOLDS_V1["boat"]
    return ScoredPairV1(
        pair=pair, entity_type="boat", deterministic_score=score,
        model_score=None, model_weight=0.0, score=score,
        feature_contributions=(), missing_features=(), thresholds=t,
    )


def _labelled(scores_matches, scores_nonmatches, high_cost_nonmatch_scores=()):
    out = []
    for i, s in enumerate(scores_matches):
        out.append(LabelledPair(scored=_mk_scored(s, (f"m{i}a", f"m{i}b")), is_match=True))
    for i, s in enumerate(scores_nonmatches):
        out.append(LabelledPair(
            scored=_mk_scored(s, (f"n{i}a", f"n{i}b")), is_match=False,
            high_cost=s in set(high_cost_nonmatch_scores)))
    return out


class TestFitThresholds:
    def test_reject_line_keeps_recall(self):
        labelled = _labelled([0.5, 0.6, 0.7, 0.8], [0.05, 0.1, 0.12, 0.18])
        t = fit_thresholds(labelled, min_recall=1.0)
        # every match must survive the reject line
        assert all(s >= t.auto_reject_below for s in (0.5, 0.6, 0.7, 0.8))

    def test_merge_line_above_highest_high_cost_nonmatch(self):
        labelled = _labelled([0.5, 0.9, 0.95], [0.1, 0.85], high_cost_nonmatch_scores=(0.85,))
        t = fit_thresholds(labelled)
        assert t.auto_merge_at_or_above > 0.85

    def test_fit_is_reproducible(self):
        labelled = _labelled([0.6, 0.7, 0.8], [0.1, 0.2, 0.3])
        a = fit_thresholds(labelled)
        b = fit_thresholds(labelled)
        assert a == b
        assert a.fit_pairs == len(labelled) and a.fit_fingerprint

    def test_fit_requires_examples(self):
        with pytest.raises(ScoringError):
            fit_thresholds([])

    def test_band_is_strictly_valid(self):
        labelled = _labelled([0.99], [0.98], high_cost_nonmatch_scores=(0.98,))
        t = fit_thresholds(labelled)
        assert t.auto_reject_below < t.auto_merge_at_or_above


# ---------------------------------------------------------------------------
# Uncertain band → adjudication routing (DP-04-05 integration)
# ---------------------------------------------------------------------------


class TestRouting:
    def test_bands_cover_unit_interval(self):
        t = ThresholdConfig(auto_reject_below=0.2, auto_merge_at_or_above=0.9)
        bands = {_mk_scored(s).routing_band for s in
                 (0.0, 0.1, 0.19, 0.2, 0.5, 0.89, 0.9, 1.0)}
        # force thresholds on the helper pairs
        def band(s):
            return ScoredPairV1(pair=_pair("a", "b"), entity_type="boat",
                                deterministic_score=s, model_score=None,
                                model_weight=0.0, score=s,
                                feature_contributions=(), missing_features=(),
                                thresholds=t).routing_band
        assert band(0.1) == "auto_reject"
        assert band(0.5) == "uncertain"
        assert band(0.9) == "auto_merge"

    def test_uncertain_band_routes_to_adjudication(self):
        # The DP-04-05 queue must agree: a score inside the band queues a
        # human; outside it does not.  We route three *real* scored pairs
        # whose deterministic scores land in each band.
        from irc_data.matching.adjudication import (
            AdjudicationQueue, ScoredCandidateV1,
        )
        t = ThresholdConfig(auto_reject_below=0.15, auto_merge_at_or_above=0.9)
        config = ScoringConfig(thresholds=t)
        scorer = PairwiseScorer(config)

        # low band: a single weak (corroboration-only) pair scores ~0.0
        low = scorer.score_pair(_pair("a", "b"), obs("a"), obs("b"))
        # uncertain band: sail-only evidence scores 0.22
        mid = scorer.score_pair(_pair("c", "d"),
                                obs("c", sail_number="AUS 4343"),
                                obs("d", sail_number="EAUS4343"))
        # high band: everything agrees
        high = scorer.score_pair(
            _pair("e", "f", ("R01", "R02", "R05")),
            obs("e", sail_number="AUS 4343", registry_id="R1", name="X",
                design="D", country="AUS", loa_m=10.0, beam_m=3.5,
                year_built=2001, valid_from=date(2001, 1, 1)),
            obs("f", sail_number="AUS4343", registry_id="R1", name="X",
                design="D", country="AUS", loa_m=10.0, beam_m=3.5,
                year_built=2001, valid_from=date(2001, 6, 1)))

        assert low.routing_band == "auto_reject"
        assert mid.routing_band == "uncertain"
        assert high.routing_band == "auto_merge"

        q = AdjudicationQueue()
        assert q.enqueue(ScoredCandidateV1(**low.to_scored_candidate_kwargs())) is None
        assert q.enqueue(ScoredCandidateV1(**mid.to_scored_candidate_kwargs())) is not None
        assert q.enqueue(ScoredCandidateV1(**high.to_scored_candidate_kwargs())) is None

    def test_handoff_carries_explanation(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"))
        kwargs = sp.to_scored_candidate_kwargs()
        assert kwargs["score"] == sp.score
        assert kwargs["score_explanation"] == sp.explanation
        assert kwargs["pair"] is sp.pair


# ---------------------------------------------------------------------------
# Holdout evaluation
# ---------------------------------------------------------------------------


class TestHoldoutEvaluation:
    def test_precision_recall_and_high_cost_false_merges(self):
        labelled = _labelled(
            [0.95, 0.93, 0.55],               # 3 matches (one in the band)
            [0.10, 0.30, 0.92],               # 3 non-matches (one scored high)
            high_cost_nonmatch_scores=(0.92,),
        )
        t = ThresholdConfig(auto_reject_below=0.2, auto_merge_at_or_above=0.9)
        m = evaluate_holdout(labelled, t)
        # decision line 0.9: predicted merges = {0.95, 0.93, 0.92}
        assert m.true_positives == 2 and m.false_positives == 1
        assert m.precision == pytest.approx(2 / 3)
        assert m.recall == pytest.approx(2 / 3)
        # the 0.92 false positive was high-cost
        assert m.high_cost_false_merges == 1
        # 0.55 (match) and 0.30 (non-match) sit in the uncertain band
        assert m.uncertain == 2

    def test_calibration_bins_cover_unit_interval(self):
        labelled = _labelled([0.05, 0.15, 0.5, 0.95], [0.02, 0.25, 0.4, 0.6])
        m = evaluate_holdout(labelled, DEFAULT_THRESHOLDS_V1["boat"], bins=10)
        assert len(m.calibration) == 10
        assert 0.0 <= m.expected_calibration_error <= 1.0
        # well-separated data → low calibration error in populated bins
        populated = [b for b in m.calibration if b.count]
        assert populated

    def test_deterministic_split_reproducible(self):
        labelled = _labelled([0.6, 0.7, 0.8, 0.9, 0.55],
                             [0.1, 0.2, 0.3, 0.15, 0.25])
        cal1, hold1 = split_labelled(labelled)
        cal2, hold2 = split_labelled(labelled)
        assert cal1 == cal2 and hold1 == hold2
        assert set(cal1) | set(hold1) == set(labelled)
        assert not (set(cal1) & set(hold1))

    def test_report_serialises(self):
        labelled = _labelled([0.95, 0.5], [0.1, 0.92],
                             high_cost_nonmatch_scores=(0.92,))
        m = evaluate_holdout(labelled, DEFAULT_THRESHOLDS_V1["boat"])
        d = m.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION
        assert {"precision", "recall", "high_cost_false_merges",
                "expected_calibration_error", "calibration"} <= set(d)


# ---------------------------------------------------------------------------
# Contract: ScoredPairV1 validation
# ---------------------------------------------------------------------------


class TestContractValidation:
    def test_score_must_be_unit_interval(self):
        with pytest.raises(ScoringError):
            _mk_scored(1.5)

    def test_deterministic_score_must_be_unit_interval(self):
        with pytest.raises(ScoringError):
            ScoredPairV1(pair=_pair("a", "b"), entity_type="boat",
                         deterministic_score=-0.1, model_score=None,
                         model_weight=0.0, score=0.5,
                         feature_contributions=(), missing_features=(),
                         thresholds=DEFAULT_THRESHOLDS_V1["boat"])

    def test_scored_pair_serialises(self):
        sp = _score(obs("a", sail_number="AUS 4343"), obs("b", sail_number="AUS4343"))
        d = sp.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION
        assert d["scorer_ruleset_id"] == SCORER_RULESET_V1_ID
        assert "feature_contributions" in d and "missing_features" in d
        assert d["routing_band"] in ("auto_reject", "uncertain", "auto_merge")
