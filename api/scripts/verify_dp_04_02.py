#!/usr/bin/env python3
"""End-to-end verification evidence for DP-04-02 — deterministic blocking
and candidate generation.

Runs the shipped ``blocking-rules-v1`` ruleset against two evaluation
corpora and prints hard, paste-able PASS/FAIL evidence for the issue
board:

  1. **Hand-labelled corpus** — seven messy real-world duplicate cases
     (sail-number prefix variants, registry-id drift, design+name,
     dimension rounding, name case drift, name typo + geography,
     design-era overlap) plus 40 unique distractors.  Asserts recall = 1.0
     and that every fired rule is represented.
  2. **Production-scale corpus** — 2 000 observations with 200 planted
     duplicates across 1 800 uniques.  Asserts the dataset-specific
     targets: recall = 1.0, candidate pair ratio ≤ 1 % of all pairs, and
     runtime within budget — demonstrating candidates are found *without
     all-pairs comparison*.
  3. **Contract checks** — every candidate records which rules fired;
     the run is deterministic; the ruleset is versioned.

No database or network required — the candidate generator is pure.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_04_02.py
"""

from __future__ import annotations

import random
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.matching.blocking import (  # noqa: E402
    BLOCKING_RULESET_V1,
    KNOWN_RULESETS,
    RULESET_V1_ID,
    CandidateGenerator,
    EntityObservation,
    EvaluationTargets,
    evaluate_candidates,
    get_ruleset,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


def obs(obs_id: str, **kwargs) -> EntityObservation:
    return EntityObservation(observation_id=obs_id, **kwargs)


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------------------
# Corpus builders
# ---------------------------------------------------------------------------


def labelled_corpus() -> tuple[list[EntityObservation], set[tuple[str, str]]]:
    """Seven hand-labelled duplicates, one per blocking rule."""
    observations: list[EntityObservation] = []
    matches: set[tuple[str, str]] = set()

    def add_pair(left: EntityObservation, right: EntityObservation) -> None:
        observations.extend([left, right])
        matches.add((left.observation_id, right.observation_id))

    add_pair(obs("irc-1", sail_number="AUS 4343", name="Wild Oats XI"),
             obs("orc-1", sail_number="EAUS4343"))
    add_pair(obs("a-2", registry_id="AUSYC12345", name="Old Name"),
             obs("b-2", registry_id="ausyc12345", name="New Name"))
    add_pair(obs("a-3", design="Sydney 38", name="Wicked"),
             obs("b-3", design="Sydney 38", name="Wicked"))
    add_pair(obs("a-4", design="J/122", loa_m=12.19),
             obs("b-4", design="J122", loa_m=12.2))
    add_pair(obs("a-5", name="Black Jack"),
             obs("b-5", name="BLACK  JACK"))
    add_pair(obs("a-6", name="Ragamuffin", country="AUS"),
             obs("b-6", name="Raggamuffin", country="AUS"))
    add_pair(obs("a-7", design="Farr 40", year_built=2008),
             obs("b-7", design="Farr 40", valid_from=date(2009, 3, 1)))

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


def production_scale_corpus(
    n_unique: int = 1800, n_dupes: int = 200, seed: int = 20240502,
) -> tuple[list[EntityObservation], set[tuple[str, str]]]:
    """Synthetic production-scale corpus with planted duplicates."""
    rng = random.Random(seed)
    syllables_a = ["Al", "Bar", "Cor", "Dal", "El", "Fal", "Gar", "Hal",
                   "In", "Jar", "Kel", "Lor", "Mar", "Nor", "Ost", "Par",
                   "Quin", "Ran", "Sal", "Tar", "Ul", "Val", "Wyn", "Zel"]
    syllables_b = ["andra", "bella", "cora", "dora", "elia", "fina",
                   "gale", "hara", "ira", "jade", "kara", "luna", "mia",
                   "nessa", "ora", "piper", "quest", "rosa", "star",
                   "tide", "umber", "viper", "wave", "xen"]
    names = [f"{a}{b}" for a in syllables_a for b in syllables_b]
    rng.shuffle(names)

    observations: list[EntityObservation] = []
    matches: set[tuple[str, str]] = set()
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
    for k in range(n_dupes):
        base = observations[rng.randrange(n_unique)]
        mode = k % 7
        if mode == 0:
            dup_kwargs = {"sail_number": base.sail_number.replace(" ", "-")}
        elif mode == 1:
            dup_kwargs = {"registry_id": base.registry_id}
        elif mode == 2:
            dup_kwargs = {"design": base.design, "name": base.name}
        elif mode == 3:
            dup_kwargs = {"design": base.design, "loa_m": base.loa_m}
        elif mode == 4:
            dup_kwargs = {"name": base.name.lower()}
        elif mode == 5:
            dup_kwargs = {"name": base.name, "country": base.country}
        else:
            dup_kwargs = {"design": base.design, "year_built": base.year_built}
        dup_id = f"dup-{k}"
        observations.append(obs(dup_id, **dup_kwargs))
        matches.add((base.observation_id, dup_id))
    return observations, matches


# ---------------------------------------------------------------------------
# Verification flow
# ---------------------------------------------------------------------------


def main() -> int:
    _banner("DP-04-02 — deterministic blocking & candidate generation")
    print(f"  ruleset id            : {RULESET_V1_ID}")
    print(f"  known rulesets        : {', '.join(KNOWN_RULESETS)}")
    print(f"  ruleset fingerprint   : {BLOCKING_RULESET_V1.fingerprint()}")
    print(f"  rules                 : "
          f"{', '.join(f'{r.rule_id}={r.name}' for r in BLOCKING_RULESET_V1.rules)}")

    # -- 0. Ruleset contract -------------------------------------------------
    _banner("0. Versioned ruleset")
    check("ruleset is versioned and fetchable",
          get_ruleset(RULESET_V1_ID) is BLOCKING_RULESET_V1)
    check("seven rules shipped",
          BLOCKING_RULESET_V1.rule_ids() ==
          ("R01", "R02", "R03", "R04", "R05", "R06", "R07"))

    # -- 1. Hand-labelled corpus ---------------------------------------------
    _banner("1. Hand-labelled corpus (7 messy duplicate cases + 40 uniques)")
    observations, matches = labelled_corpus()
    gen = CandidateGenerator()
    report = gen.generate(observations)
    result = evaluate_candidates(
        report, matches,
        targets=EvaluationTargets(min_recall=1.0, max_pair_ratio=0.25,
                                  max_runtime_seconds=10.0),
    )
    print(f"  observations={result.all_pairs and report.stats.observations} "
          f"all_pairs={result.all_pairs} candidates={result.candidates} "
          f"recall={result.recall:.3f} precision_ceiling={result.precision_ceiling:.3f} "
          f"runtime={result.runtime_seconds:.3f}s")
    check("known-match recall = 1.0", result.recall == 1.0,
          f"missed={list(result.missed_pairs)}")
    check("candidate volume within target", result.volume_ok,
          f"pair_ratio={result.pair_ratio:.4f} <= 0.25")
    fired = {r for p in report.pairs for r in p.rules_fired}
    check("every rule fired on the corpus",
          fired == set(BLOCKING_RULESET_V1.rule_ids()),
          f"fired={sorted(fired)}")

    # -- 2. Production-scale corpus ------------------------------------------
    _banner("2. Production-scale corpus (2 000 observations, 200 planted dupes)")
    observations, matches = production_scale_corpus()
    gen = CandidateGenerator()
    started = time.monotonic()
    report = gen.generate(observations)
    wall = time.monotonic() - started
    result = evaluate_candidates(
        report, matches,
        targets=EvaluationTargets(min_recall=1.0, max_pair_ratio=0.01,
                                  max_runtime_seconds=20.0),
    )
    print(f"  observations={report.stats.observations} "
          f"all_pairs={result.all_pairs:,} candidates={result.candidates:,} "
          f"recall={result.recall:.3f} pair_ratio={result.pair_ratio:.5f} "
          f"runtime={result.runtime_seconds:.3f}s (wall {wall:.3f}s)")
    check("known-match recall = 1.0 at scale", result.recall == 1.0,
          f"missed={list(result.missed_pairs[:5])}")
    check("candidate volume ≤ 1% of all pairs", result.volume_ok,
          f"pair_ratio={result.pair_ratio:.5f}")
    check("runtime within budget (20 s)", result.runtime_ok and wall < 20.0,
          f"reported={result.runtime_seconds:.3f}s wall={wall:.3f}s")
    check("no oversized-block recall loss at scale",
          report.stats.skipped_oversized_blocks == 0 or result.recall == 1.0,
          f"skipped_blocks={report.stats.skipped_oversized_blocks}")

    # -- 3. Contract checks ----------------------------------------------------
    _banner("3. Output contract")
    check("every candidate records which rules fired",
          all(p.rules_fired for p in report.pairs))
    check("ruleset id recorded on the report",
          report.ruleset_id == RULESET_V1_ID)
    again = CandidateGenerator().generate(production_scale_corpus()[0])
    check("candidate generation is deterministic",
          [(p.left_id, p.right_id, p.rules_fired) for p in report.pairs] ==
          [(p.left_id, p.right_id, p.rules_fired) for p in again.pairs])

    # -- Summary ----------------------------------------------------------------
    _banner("Summary")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"  {passed}/{total} checks passed")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label}  {detail}")
    print("\nRESULT:", "PASS" if passed == total else "FAIL")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
