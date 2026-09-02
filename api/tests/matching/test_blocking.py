"""Tests for DP-04-02 — deterministic blocking and candidate generation.

Covers:

* each of the seven ``blocking-rules-v1`` rules individually,
* the candidate contract (every candidate records which rules fired),
* determinism / reproducibility of the candidate set,
* the evaluation harness (recall, precision ceiling, pair ratio, runtime)
  against a labelled corpus including a production-scale synthetic set.
"""

from __future__ import annotations

import random
import time
from datetime import date

import pytest

from irc_data.matching.blocking import (
    BLOCKING_RULESET_V1,
    DEFAULT_MAX_BLOCK_SIZE,
    KNOWN_RULESETS,
    RULESET_V1_ID,
    BlockingError,
    BlockingRule,
    BlockingRuleset,
    CandidateGenerator,
    CandidatePair,
    EntityObservation,
    EvaluationTargets,
    UnknownRulesetError,
    evaluate_candidates,
    get_ruleset,
    normalize_design_key,
    normalize_name_key,
    normalize_registry_id,
    _name_tokens,
    _soundex,
)


def obs(obs_id: str, **kwargs) -> EntityObservation:
    return EntityObservation(observation_id=obs_id, **kwargs)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_name_key_folds_accents_and_case(self):
        assert normalize_name_key("Émile  XI") == "EMILE XI"
        assert normalize_name_key("wild-oats xi") == "WILD OATS XI"

    def test_name_key_empty(self):
        assert normalize_name_key(None) == ""
        assert normalize_name_key("   ") == ""

    def test_name_tokens_strip_stopwords(self):
        assert _name_tokens("The Spirit of II") == ("SPIRIT",)
        assert _name_tokens(None) == ()

    def test_soundex_known_values(self):
        # Classic Soundex reference values.
        assert _soundex("ROBERT") == "R163"
        assert _soundex("RUPERT") == "R163"
        assert _soundex("ASHCRAFT") == "A261"
        assert _soundex("RAGAMUFFIN") == _soundex("RAGGAMUFFIN")

    def test_soundex_rejects_non_alpha(self):
        assert _soundex("123") == ""
        assert _soundex("") == ""

    def test_registry_id_normalisation(self):
        assert normalize_registry_id(" au-xyz 12/34 ") == "AUXYZ1234"
        assert normalize_registry_id(None) == ""

    def test_design_key_collapses_punctuation(self):
        assert normalize_design_key("J/122") == "J 122"
        assert normalize_design_key("Sun Fast 3300") == "SUN FAST 3300"
        assert normalize_design_key("Unknown") == ""  # generic → no block


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


class TestSailNumberTokenRule:
    def test_prefix_variants_share_block(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", sail_number="EAUS1213"),
            obs("b", sail_number="AUS 1213"),
        ])
        pair = report.pairs[0]
        assert pair.rules_fired == ("R01",)
        assert pair.matching_keys  # shared block key recorded
        assert all(k.startswith("R01:") for k in pair.matching_keys)

    def test_bare_short_sail_is_country_guarded(self):
        gen = CandidateGenerator()
        # Bare 4-digit sails without countries must NOT block (ambiguous).
        report = gen.generate([obs("a", sail_number="4343"), obs("b", sail_number="4343")])
        assert report.pairs == ()
        # ... but with the same country they do.
        report = gen.generate([
            obs("a", sail_number="4343", country="AUS"),
            obs("b", sail_number="4343", country="aus"),
        ])
        assert [p.rules_fired for p in report.pairs] == [("R01",)]
        # Different countries stay apart.
        report = gen.generate([
            obs("a", sail_number="4343", country="AUS"),
            obs("b", sail_number="4343", country="GBR"),
        ])
        assert report.pairs == ()

    def test_no_sail_no_keys(self):
        rule = get_ruleset().rules[0]
        assert rule.keys_for(obs("a")) == ()


class TestRegistryIdRule:
    def test_registry_ids_match_after_normalisation(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", registry_id="AU-XYZ 12/34"),
            obs("b", registry_id="auxyz1234"),
        ])
        assert [p.rules_fired for p in report.pairs] == [("R02",)]

    def test_missing_registry_emits_nothing(self):
        rule = {r.rule_id: r for r in get_ruleset().rules}["R02"]
        assert rule.keys_for(obs("a", registry_id=None)) == ()


class TestDesignExactRule:
    def test_design_plus_name_token_blocks(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", design="Sydney 38", name="Wicked"),
            obs("b", design="Sydney 38", name="Wicked"),
        ])
        assert "R03" in report.pairs[0].rules_fired

    def test_design_alone_does_not_block(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", design="Beneteau First 40.7"),
            obs("b", design="Beneteau First 40.7"),
        ])
        assert report.pairs == ()


class TestDimensionsBandRule:
    def test_loa_band_absorbs_rounding(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", design="J/122", loa_m=12.19),
            obs("b", design="J 122", loa_m=12.2),
        ])
        assert "R04" in report.pairs[0].rules_fired

    def test_adjacent_band_boundary_covered(self):
        gen = CandidateGenerator()
        # 10.249 and 10.251 straddle the 10.25 band boundary.
        report = gen.generate([
            obs("a", design="JPK 1010", loa_m=10.249),
            obs("b", design="JPK 1010", loa_m=10.251),
        ])
        assert "R04" in report.pairs[0].rules_fired

    def test_requires_design_and_loa(self):
        rule = {r.rule_id: r for r in get_ruleset().rules}["R04"]
        assert rule.keys_for(obs("a", loa_m=10.0)) == ()
        assert rule.keys_for(obs("a", design="J/122")) == ()


class TestNameExactRule:
    def test_case_and_whitespace_variants_block(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", name="Wild Oats XI"),
            obs("b", name="WILD  OATS  XI"),
        ])
        assert [p.rules_fired for p in report.pairs] == [("R05",)]

    def test_tiny_names_ignored(self):
        rule = {r.rule_id: r for r in get_ruleset().rules}["R05"]
        assert rule.keys_for(obs("a", name="XI")) == ()


class TestNameSoundexGeoRule:
    def test_typo_variant_blocks_within_country(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", name="Ragamuffin", country="AUS"),
            obs("b", name="Raggamuffin", country="AUS"),
        ])
        assert "R06" in report.pairs[0].rules_fired

    def test_different_countries_do_not_block(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", name="Ragamuffin", country="AUS"),
            obs("b", name="Raggamuffin", country="NZL"),
        ])
        assert all("R06" not in p.rules_fired for p in report.pairs)


class TestTemporalOverlapDesignRule:
    def test_overlapping_eras_block(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", design="Sydney 38", year_built=2001),
            obs("b", design="Sydney 38", valid_from=date(2003, 5, 1),
                valid_to=date(2004, 9, 30)),
        ])
        assert "R07" in report.pairs[0].rules_fired

    def test_distant_eras_stay_apart(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", design="Sydney 38", year_built=1998),
            obs("b", design="Sydney 38", year_built=2019),
        ])
        assert all("R07" not in p.rules_fired for p in report.pairs)

    def test_no_temporal_signal_no_key(self):
        rule = {r.rule_id: r for r in get_ruleset().rules}["R07"]
        assert rule.keys_for(obs("a", design="Sydney 38")) == ()


# ---------------------------------------------------------------------------
# Ruleset versioning
# ---------------------------------------------------------------------------


class TestRulesetVersioning:
    def test_default_ruleset_is_v1(self):
        gen = CandidateGenerator()
        assert gen.ruleset.ruleset_id == RULESET_V1_ID
        assert gen.ruleset.rule_ids() == ("R01", "R02", "R03", "R04", "R05", "R06", "R07")

    def test_unknown_ruleset_raises(self):
        with pytest.raises(UnknownRulesetError):
            get_ruleset("blocking-rules-v99")

    def test_known_rulesets_listed(self):
        assert RULESET_V1_ID in KNOWN_RULESETS

    def test_fingerprint_stable(self):
        assert BLOCKING_RULESET_V1.fingerprint() == get_ruleset().fingerprint()
        assert len(get_ruleset().fingerprint()) == 16

    def test_ruleset_is_immutable(self):
        with pytest.raises(AttributeError):
            BLOCKING_RULESET_V1.ruleset_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Candidate contract
# ---------------------------------------------------------------------------


class TestCandidateContract:
    def test_every_candidate_records_rules_fired(self):
        gen = CandidateGenerator()
        report = gen.generate([
            obs("a", sail_number="GBR 8310", name="Wild Thing",
                design="Maxi", country="GBR", registry_id="HIN123"),
            obs("b", sail_number="GBR8310", name="Wild Thing",
                design="Maxi", country="GBR", registry_id="HIN123"),
        ])
        assert len(report.pairs) == 1
        pair = report.pairs[0]
        assert set(pair.rules_fired) >= {"R01", "R02", "R05"}
        assert pair.rules_fired == tuple(sorted(pair.rules_fired))
        assert pair.matching_keys  # shared block keys recorded
        assert pair.ruleset_id == RULESET_V1_ID

    def test_pair_requires_distinct_ids_and_rules(self):
        with pytest.raises(BlockingError):
            CandidatePair(left_id="x", right_id="x", rules_fired=("R01",),
                          matching_keys=())
        with pytest.raises(BlockingError):
            CandidatePair(left_id="x", right_id="y", rules_fired=(),
                          matching_keys=())

    def test_pair_serialisation_round_shape(self):
        pair = CandidatePair(left_id="a", right_id="b", rules_fired=("R01",),
                             matching_keys=("R01:X",))
        d = pair.to_dict()
        assert d["schema_version"] == "blocking-v1"
        assert d["rules_fired"] == ["R01"]

    def test_observation_validation(self):
        with pytest.raises(BlockingError):
            obs("")
        with pytest.raises(BlockingError):
            obs("a", loa_m=-1)
        with pytest.raises(BlockingError):
            obs("a", year_built=1200)

    def test_observation_dict_round_trip(self):
        original = obs("a", sail_number="GBR 1", valid_from=date(2020, 1, 1))
        assert EntityObservation.from_dict(original.to_dict()) == original

    def test_duplicate_observation_ids_rejected(self):
        gen = CandidateGenerator()
        with pytest.raises(BlockingError):
            gen.generate([obs("dup", name="Alpha"), obs("dup", name="Alpha")])

    def test_determinism_same_input_same_candidates(self):
        observations = [
            obs(f"o{i}", sail_number=f"AUS {1000 + (i % 37)}",
                name=f"Boat {i % 11}", design="Sydney 38",
                country="AUS", loa_m=11.4 + (i % 3) * 0.01,
                year_built=2000 + (i % 20))
            for i in range(300)
        ]
        gen = CandidateGenerator()
        first = gen.generate(observations)
        second = gen.generate(reversed(observations))
        assert [(p.left_id, p.right_id, p.rules_fired) for p in first.pairs] == [
            (p.left_id, p.right_id, p.rules_fired) for p in second.pairs
        ]

    def test_oversized_blocks_skipped_and_recorded(self):
        gen = CandidateGenerator(max_block_size=5)
        # Ten boats sharing one exact name → block of 10 > cap of 5.
        report = gen.generate([obs(f"o{i}", name="Blue Eyes") for i in range(10)])
        assert report.pairs == ()
        assert report.stats.skipped_oversized_blocks == 1

    def test_max_block_size_floor(self):
        with pytest.raises(BlockingError):
            CandidateGenerator(max_block_size=1)


# ---------------------------------------------------------------------------
# Evaluation harness — the verification acceptance criteria
# ---------------------------------------------------------------------------


def _labelled_corpus() -> tuple[list[EntityObservation], set[tuple[str, str]]]:
    """Small hand-labelled corpus with known duplicate pairs.

    Every duplicate pair is constructed to exercise a different rule (or
    rule combination), mirroring the messy real-world cases of the DP-03
    domain review.
    """
    observations: list[EntityObservation] = []
    matches: set[tuple[str, str]] = set()

    def add_pair(left: EntityObservation, right: EntityObservation) -> None:
        observations.extend([left, right])
        matches.add((left.observation_id, right.observation_id))

    # R01 — sail-number prefix variants (ORC cert vs IRC boat record).
    add_pair(obs("irc-1", sail_number="AUS 4343", name="Wild Oats XI"),
             obs("orc-1", sail_number="EAUS4343"))
    # R02 — same registry/hull id, everything else drifted.
    add_pair(obs("a-2", registry_id="AUSYC12345", name="Old Name"),
             obs("b-2", registry_id="ausyc12345", name="New Name"))
    # R03 — same design + same first name token.
    add_pair(obs("a-3", design="Sydney 38", name="Wicked"),
             obs("b-3", design="Sydney 38", name="Wicked"))
    # R04 — same design family, LOA differing by certificate rounding.
    add_pair(obs("a-4", design="J/122", loa_m=12.19),
             obs("b-4", design="J122", loa_m=12.2))
    # R05 — exact name despite case/whitespace drift.
    add_pair(obs("a-5", name="Black Jack"),
             obs("b-5", name="BLACK  JACK"))
    # R06 — name typo within one country.
    add_pair(obs("a-6", name="Ragamuffin", country="AUS"),
             obs("b-6", name="Raggamuffin", country="AUS"))
    # R07 — same design, overlapping eras.
    add_pair(obs("a-7", design="Farr 40", year_built=2008),
             obs("b-7", design="Farr 40", valid_from=date(2009, 3, 1)))

    # Distractors: unique boats that must not inflate the candidate set.
    # Names are distinctive so no soundex block coalesces them.
    distractor_names = [
        "Albatross", "Bandit", "Circe", "Delphine", "Escapade", "Fandango",
        "Gwalch", "Halcyon", "Iolanthe", "Jorunn", "Kestrel", "Lorelei",
        "Mistral", "Naiad", "Ondine", "Pelican", "Quokka", "Rocinante",
        "Sirocco", "Tempest", "Undine", "Valkyrie", "Wanderer", "Xanthe",
        "Yare", "Zephyr", "Ariel", "Boreas", "Calypso", "Drifter", "Eala",
        "Fulmar", "Gannet", "Hobgoblin", "Iskra", "Jester", "Kraken",
        "Lyra", "Maelstrom", "Nixie", "Osprey",
    ]
    for i, name in enumerate(distractor_names):
        observations.append(obs(
            f"uniq-{i}", sail_number=f"GBR {9000 + i}",
            name=name, design=f"Design {i}", country="GBR",
            loa_m=8.0 + i * 0.37, year_built=1990 + i,
        ))
    return observations, matches


class TestEvaluationHarness:
    def test_known_match_recall_and_volume_on_corpus(self):
        observations, matches = _labelled_corpus()
        gen = CandidateGenerator()
        report = gen.generate(observations)
        result = evaluate_candidates(
            report, matches,
            targets=EvaluationTargets(min_recall=1.0, max_pair_ratio=0.25,
                                      max_runtime_seconds=10.0),
        )
        assert result.missed_pairs == ()
        assert result.recall == 1.0
        assert result.recall_ok and result.volume_ok and result.runtime_ok
        assert result.passed()
        assert 0.0 < result.precision_ceiling <= 1.0
        assert result.reduction_ratio > 0.9  # all-pairs mostly eliminated
        # Every fired rule is represented on the labelled pairs.
        fired = {r for p in report.pairs for r in p.rules_fired}
        assert fired == set(gen.ruleset.rule_ids())

    def test_missed_pairs_reported(self):
        report = CandidateGenerator().generate([
            obs("a", name="Alpha"), obs("b", name="Beta"),
        ])
        result = evaluate_candidates(report, [("a", "b")])
        assert result.recall == 0.0
        assert result.missed_pairs == (("a", "b"),)
        assert not result.passed()

    def test_empty_truth_is_vacuously_perfect_recall(self):
        report = CandidateGenerator().generate([obs("a", name="Alpha")])
        result = evaluate_candidates(report, [])
        assert result.recall == 1.0

    def test_evaluation_result_serialisation(self):
        observations, matches = _labelled_corpus()
        report = CandidateGenerator().generate(observations)
        result = evaluate_candidates(report, matches)
        d = result.to_dict()
        assert d["schema_version"] == "blocking-v1"
        assert d["ruleset_id"] == RULESET_V1_ID
        assert set(d["checks"]) == {"recall_ok", "volume_ok", "runtime_ok"}

    def test_production_scale_recall_volume_and_runtime(self):
        """Production-scale synthetic corpus: 2 000 observations with 200
        planted duplicate pairs.  Asserts the dataset-specific targets:
        recall = 1.0, pair ratio ≤ 1 %, runtime ≤ 20 s."""
        rng = random.Random(20240502)
        observations: list[EntityObservation] = []
        matches: set[tuple[str, str]] = set()

        # Realistic name pool so soundex blocks stay small (each name is a
        # distinctive two-token phrase; soundex+country blocks are tiny).
        syllables_a = ["Al", "Bar", "Cor", "Dal", "El", "Fal", "Gar", "Hal",
                       "In", "Jar", "Kel", "Lor", "Mar", "Nor", "Ost", "Par",
                       "Quin", "Ran", "Sal", "Tar", "Ul", "Val", "Wyn", "Zel"]
        syllables_b = ["andra", "bella", "cora", "dora", "elia", "fina",
                       "gale", "hara", "ira", "jade", "kara", "luna", "mia",
                       "nessa", "ora", "piper", "quest", "rosa", "star",
                       "tide", "umber", "viper", "wave", "xen"]
        names = [f"{a}{b}" for a in syllables_a for b in syllables_b]
        rng.shuffle(names)

        n_unique = 1800
        for i in range(n_unique):
            observations.append(obs(
                f"u{i}",
                sail_number=f"{rng.choice(['AUS', 'GBR', 'USA', 'NZL'])} {rng.randint(1000, 999999)}",
                registry_id=f"REG{rng.randint(10**6, 10**7 - 1)}",
                name=names[i % len(names)],
                design=f"Design {i % 120}",
                country=rng.choice(["AUS", "GBR", "USA", "NZL"]),
                loa_m=7.0 + (i % 400) * 0.05,
                year_built=1980 + (i % 40),
            ))

        # Plant 200 duplicates, each mutating a different field so recall
        # depends on the union of rules, not any single one.
        for k in range(200):
            base = observations[rng.randrange(n_unique)]
            mode = k % 7
            dup_kwargs: dict = {}
            if mode == 0:      # same sail tokens, reformatted
                dup_kwargs = {"sail_number": base.sail_number.replace(" ", "-")}
            elif mode == 1:    # same registry id
                dup_kwargs = {"registry_id": base.registry_id}
            elif mode == 2:    # same design + first name token
                dup_kwargs = {"design": base.design, "name": base.name}
            elif mode == 3:    # same design + LOA within a band
                dup_kwargs = {"design": base.design, "loa_m": base.loa_m}
            elif mode == 4:    # same name, different case
                dup_kwargs = {"name": base.name.lower()}
            elif mode == 5:    # same name + country (soundex path)
                dup_kwargs = {"name": base.name, "country": base.country}
            else:              # same design + same era
                dup_kwargs = {"design": base.design, "year_built": base.year_built}
            dup_id = f"dup-{k}"
            observations.append(obs(dup_id, **dup_kwargs))
            matches.add((base.observation_id, dup_id))

        gen = CandidateGenerator()
        started = time.monotonic()
        report = gen.generate(observations)
        wall = time.monotonic() - started

        result = evaluate_candidates(
            report, matches,
            targets=EvaluationTargets(min_recall=1.0, max_pair_ratio=0.01,
                                      max_runtime_seconds=20.0),
        )

        assert result.recall == 1.0, f"missed: {result.missed_pairs[:5]}"
        assert result.pair_ratio <= 0.01
        assert result.runtime_ok
        assert wall < 20.0
        assert result.passed()
        # Volume sanity: candidates are a tiny fraction of all pairs.
        assert report.stats.candidate_pairs < report.stats.all_pairs // 50


class TestRulePurity:
    def test_all_shipped_rules_are_pure(self):
        """Rules must be pure functions of the observation (reproducibility)."""
        o = obs("x", sail_number="AUS 1", registry_id="R1", name="Test",
                design="J/122", country="AUS", loa_m=12.2, year_built=2001,
                valid_from=date(2001, 1, 1))
        for rule in BLOCKING_RULESET_V1.rules:
            assert rule.keys_for(o) == rule.keys_for(o)

    def test_custom_ruleset_accepted(self):
        class TrivialRule(BlockingRule):
            rule_id = "T01"
            name = "trivial"

            def keys_for(self, o):
                return (self._key("all"),) if o.name else ()

        ruleset = BlockingRuleset(ruleset_id="test-ruleset",
                                  rules=(TrivialRule(),))
        report = CandidateGenerator(ruleset=ruleset).generate([
            obs("a", name="One"), obs("b", name="Two"), obs("c"),
        ])
        assert report.ruleset_id == "test-ruleset"
        assert [p.rules_fired for p in report.pairs] == [("T01",)]
