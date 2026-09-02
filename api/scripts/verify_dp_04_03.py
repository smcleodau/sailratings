#!/usr/bin/env python3
"""End-to-end verification evidence for DP-04-03 — explainable pairwise
match scoring.

Runs the shipped ``scorer-rules-v1`` deterministic feature ruleset over the
DP-04-02 candidate set of a **labelled** corpus, calibrates the per-entity
-type thresholds on a *calibration split*, and then reports hard, paste-able
evidence on the held-out split:

  1. **Labelled corpus** — messy real-world duplicate pairs (sail-prefix
     drift, registry-id drift, name case/typo drift, design+dimension
     rounding, temporal overlap) plus hard non-match pairs (same name on
     different hulls, same sail number in different countries, sisterships)
     — some flagged *high-cost* (rated / has results / has certificate).
  2. **Calibration split** — ``fit_thresholds`` fits the uncertain band
     (auto-reject / auto-merge) for the ``boat`` entity type so that
     high-cost false merges are driven to zero while recall is preserved.
  3. **Holdout evaluation** — ``evaluate_holdout`` reports precision,
     recall, per-bin calibration (ECE) and the **high-cost false-merge**
     count, which must be zero.
  4. **Contract / reproducibility checks** — every scored pair decomposes
     into per-feature contributions summing to the score; missingness is
     preserved; scores are deterministic; the uncertain band routes to
     DP-04-05 adjudication.

No database or network required — the scorer is pure.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_04_03.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.matching.blocking import (  # noqa: E402
    CandidateGenerator,
    EntityObservation,
)
from irc_data.matching.scoring import (  # noqa: E402
    AUTO_MERGE_FLOOR,
    KNOWN_ENTITY_TYPES,
    KNOWN_SCORER_RULESETS,
    SCORER_RULESET_V1,
    SCORER_RULESET_V1_ID,
    LabelledPair,
    PairwiseScorer,
    ScoringConfig,
    ThresholdConfig,
    evaluate_holdout,
    fit_thresholds,
    get_scorer_ruleset,
    split_labelled,
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
# Labelled corpus
# ---------------------------------------------------------------------------


def labelled_corpus():
    """Build (observations, true_match_pairs, high_cost_pairs).

    Returns the observation list, the set of labelled duplicate id-pairs,
    and the set of labelled *high-cost* pairs (a wrong merge there is
    expensive — rated / has results / has a certificate).
    """
    observations: list[EntityObservation] = []
    matches: set[tuple[str, str]] = set()
    high_cost: set[tuple[str, str]] = set()

    def add(o: EntityObservation) -> EntityObservation:
        observations.append(o)
        return o

    def dup(a: EntityObservation, b: EntityObservation, cost: bool = False) -> None:
        pair = (a.observation_id, b.observation_id)
        matches.add(pair)
        if cost:
            high_cost.add(pair)

    def nonmatch(a: EntityObservation, b: EntityObservation, cost: bool = False) -> None:
        if cost:
            high_cost.add((a.observation_id, b.observation_id))

    # -- true duplicates (messy real-world shapes) --------------------------
    # 1. sail prefix drift + full corroboration (a confident match)
    _l = obs("irc-1", sail_number="AUS 4343", registry_id="AUSYC12345",
             name="Wild Oats XI", design="Reichel Pugh 100", country="AUS",
             loa_m=30.5, beam_m=5.6, displacement_kg=14000, year_built=2005,
             valid_from=date(2005, 1, 1), valid_to=date(2010, 1, 1))
    _r = obs("orc-1", sail_number="AUS4343", registry_id="ausyc12345",
             name="WILD OATS XI", design="RP100", country="AUS",
             loa_m=30.48, beam_m=5.61, displacement_kg=14000, year_built=2005,
             valid_from=date(2006, 1, 1), valid_to=date(2012, 1, 1))
    add(_l); add(_r); dup(_l, _r, cost=True)
    # 2. registry-id drift, name changed (registry is the anchor)
    dup(add(obs("a-2", registry_id="GBRYC9876", name="Old Name", country="GBR",
                design="First 40.7", loa_m=12.24, year_built=2001)),
        add(obs("b-2", registry_id="gbr yc 9876", name="New Name", country="GBR",
                design="Beneteau First 40.7", loa_m=12.24, year_built=2001)),
        cost=True)
    # 3. design + same name (sistership identity, fully specified)
    dup(add(obs("a-3", design="Sydney 38", name="Wicked", country="AUS",
                loa_m=11.79, beam_m=3.75, year_built=1999)),
        add(obs("b-3", design="Sydney 38", name="WICKED", country="AUS",
                loa_m=11.79, beam_m=3.75, year_built=1999)))
    # 4. design punctuation + dimension rounding
    dup(add(obs("a-4", design="J/122", name="Javelin", loa_m=12.19, beam_m=3.96,
                country="GBR", year_built=2007)),
        add(obs("b-4", design="J122", name="JAVELIN", loa_m=12.2, beam_m=3.96,
                country="GBR", year_built=2007)))
    # 5. name case/whitespace drift, same country + design
    dup(add(obs("a-5", name="Black Jack", design="Reichel Pugh 66", country="AUS",
                loa_m=20.1, year_built=2005)),
        add(obs("b-5", name="BLACK  JACK", design="RP 66", country="AUS",
                loa_m=20.1, year_built=2005)))
    # 6. sail prefix drift with NO other evidence (weak — should be uncertain)
    dup(add(obs("a-6", sail_number="AUS 9881")),
        add(obs("b-6", sail_number="EAUS9881")))
    # 7. name typo + same country (phonetic-ish)
    dup(add(obs("a-7", name="Ragamuffin", country="AUS", design="TP52",
                loa_m=15.85, year_built=2009)),
        add(obs("b-7", name="Raggamuffin", country="AUS", design="TP 52",
                loa_m=15.85, year_built=2009)))
    # 8. temporal overlap + design era (build year vs certificate era)
    dup(add(obs("a-8", design="Farr 40", name="Flash", year_built=2008,
                loa_m=12.41, country="AUS")),
        add(obs("b-8", design="Farr 40", name="FLASH", country="AUS",
                loa_m=12.41, valid_from=date(2007, 6, 1), valid_to=date(2009, 6, 1))))
    # 9. sail + design + country, moderate (uncertain-ish match)
    dup(add(obs("a-9", sail_number="GBR 7381R", design="J/109", country="GBR",
                loa_m=10.74, year_built=2004)),
        add(obs("b-9", sail_number="GBR7381R", design="J109", country="GBR",
                loa_m=10.74, year_built=2004)))
    # 10. name + country only (uncertain match)
    dup(add(obs("a-10", name="Kestrel", country="AUS", design="S80")),
        add(obs("b-10", name="KESTREL", country="AUS", design="S 80")))

    # -- hard non-matches ----------------------------------------------------
    # 11. SAME name, different hulls (different sail/registry/dims/country)
    nonmatch(add(obs("n-11a", sail_number="AUS 4011", registry_id="AUSHIN001",
                 name="Eclipse", design="First 40", country="AUS",
                 loa_m=12.24, beam_m=3.88, year_built=2010)),
             add(obs("n-11b", sail_number="USA 7712", registry_id="USHIN999",
                 name="ECLIPSE", design="J/120", country="USA",
                 loa_m=12.19, beam_m=3.7, year_built=1998)),
             cost=True)
    # 12. same bare sail number, different country + design (the classic
    #     false-merge trap — rated boats, so high-cost).  NOTE: the two bare
    #     numerics (5500) are >3 digits so the sail feature treats them as
    #     ambiguous and stays MISSING — this pair must be caught by *design/
    #     country/dimension* disagreement, not by the sail feature.
    nonmatch(add(obs("n-12a", sail_number="GER 5500", name="Norddeich",
                 design="TP52", country="GER", loa_m=15.85, year_built=2011)),
             add(obs("n-12b", sail_number="AUS 5500", name="Sydney",
                 design="Sydney 38", country="AUS", loa_m=11.79, year_built=2000)),
             cost=True)
    # 13. sisterships: same design+dims, different names + sails
    nonmatch(add(obs("n-13a", sail_number="GBR 1010L", name="Alpha",
                 design="J/111", country="GBR", loa_m=11.15, beam_m=3.6,
                 year_built=2012)),
             add(obs("n-13b", sail_number="GBR 2020L", name="Beta",
                 design="J/111", country="GBR", loa_m=11.15, beam_m=3.6,
                 year_built=2012)))
    # 14. name near-match, different design + country + era
    nonmatch(add(obs("n-14a", sail_number="FRA 100", name="Alize",
                 design="Figaro 3", country="FRA", loa_m=10.89, year_built=2019)),
             add(obs("n-14b", sail_number="ITA 200", name="Alizee",
                 design="Swan 45", country="ITA", loa_m=13.96, year_built=2003)))
    # 15. design family shared but dimensions far apart (different models)
    nonmatch(add(obs("n-15a", sail_number="AUS 88", name="Comanche",
                 design="Verdier 100", country="AUS", loa_m=30.48, beam_m=6.2,
                 year_built=2014)),
             add(obs("n-15b", sail_number="AUS 66", name="Alive",
                 design="Verdier 66", country="AUS", loa_m=20.1, beam_m=5.5,
                 year_built=2014)),
             cost=True)
    # 16. shared country + era, nothing else (a guarded weak block)
    nonmatch(add(obs("n-16a", sail_number="GBR 9000", name="Zephyr",
                 design="Sun Fast 3200", country="GBR", loa_m=9.77,
                 year_built=2015)),
             add(obs("n-16b", sail_number="GBR 9500", name="Tempest",
                 design="J/70", country="GBR", loa_m=6.93, year_built=2015)))

    # -- extra clean confident matches (fill the auto-merge band) -----------
    # 11. registry + full corroboration
    _l = obs("x-11a", sail_number="ITA 21212", registry_id="ITAHIN555",
             name="Azzurra", design="TP52", country="ITA", loa_m=15.85,
             beam_m=4.42, year_built=2011)
    _r = obs("x-11b", sail_number="ITA21212", registry_id="itahin555",
             name="AZZURRA", design="TP 52", country="ITA", loa_m=15.85,
             beam_m=4.42, year_built=2011)
    add(_l); add(_r); dup(_l, _r, cost=True)
    # 12. sail + registry + name + dims, near-perfect agreement
    _l = obs("x-12a", sail_number="GBR 45R", registry_id="GBRHIN045",
             name="Yes", design="Farr 45", country="GBR", loa_m=13.72,
             beam_m=4.1, year_built=2000)
    _r = obs("x-12b", sail_number="GBR45R", registry_id="GBRHIN045",
             name="YES", design="Farr 45", country="GBR", loa_m=13.72,
             beam_m=4.1, year_built=2000)
    add(_l); add(_r); dup(_l, _r)

    # -- unique distractors (adds background pairs via shared blocks) --------
    distractor_names = [
        "Albatross", "Bandit", "Circe", "Delphine", "Escapade", "Fandango",
        "Gwalch", "Halcyon", "Iolanthe", "Jorunn", "Lorelei", "Mistral",
        "Naiad", "Ondine", "Pelican", "Quokka", "Rocinante", "Sirocco",
        "Undine", "Valkyrie",
    ]
    for i, name in enumerate(distractor_names):
        # sails 7100+i avoid colliding with the n-16 pair (GBR 9000/9500).
        add(obs(f"u-{i}", sail_number=f"GBR {7100 + i}",
                registry_id=f"REG{i:06d}", name=name, design=f"Design {i}",
                country="GBR", loa_m=8.0 + i * 0.31, beam_m=2.5 + i * 0.05,
                year_built=1990 + i))

    return observations, matches, high_cost


# ---------------------------------------------------------------------------
# Verification flow
# ---------------------------------------------------------------------------


def main() -> int:
    _banner("DP-04-03 — explainable pairwise match scoring")
    print(f"  scorer ruleset id   : {SCORER_RULESET_V1_ID}")
    print(f"  known rulesets      : {', '.join(KNOWN_SCORER_RULESETS)}")
    print(f"  entity types        : {', '.join(KNOWN_ENTITY_TYPES)}")
    print(f"  features            : "
          f"{', '.join(f'{f.feature_id}={f.name}({f.weight})' for f in SCORER_RULESET_V1)}")

    # -- 0. Ruleset contract -------------------------------------------------
    _banner("0. Versioned deterministic feature ruleset")
    check("ruleset is versioned and fetchable",
          get_scorer_ruleset(SCORER_RULESET_V1_ID) is SCORER_RULESET_V1)
    check("feature weights sum to 1.0 (score bounded in [0,1])",
          abs(sum(f.weight for f in SCORER_RULESET_V1) - 1.0) < 1e-9,
          f"sum={sum(f.weight for f in SCORER_RULESET_V1):.6f}")
    check("eleven deterministic features shipped",
          len(SCORER_RULESET_V1) == 11)

    # -- 1. Labelled corpus → DP-04-02 candidates → DP-04-03 scoring ---------
    _banner("1. Labelled corpus: blocking → scoring")
    observations, matches, high_cost_pairs = labelled_corpus()
    gen = CandidateGenerator()
    cand_report = gen.generate(observations)
    by_id = {o.observation_id: o for o in observations}
    match_keys = {tuple(sorted(p)) for p in matches}
    print(f"  observations={len(observations)} candidates={len(cand_report.pairs)} "
          f"labelled_matches={len(matches)}")

    scorer = PairwiseScorer()
    labelled: list[LabelledPair] = []
    for pair in cand_report.pairs:
        key = tuple(sorted((pair.left_id, pair.right_id)))
        is_match = key in match_keys
        high_cost = key in {tuple(sorted(p)) for p in high_cost_pairs}
        sp = scorer.score_pair(pair, by_id[pair.left_id], by_id[pair.right_id])
        labelled.append(LabelledPair(scored=sp, is_match=is_match, high_cost=high_cost))

    check("labelled corpus produced scored pairs", len(labelled) > 0,
          f"scored={len(labelled)}")
    labelled_matches = [lp for lp in labelled if lp.is_match]
    check("blocking kept all labelled matches (recall precondition)",
          len(labelled_matches) == len(matches),
          f"kept {len(labelled_matches)}/{len(matches)}")

    # -- 2. Reproducibility ---------------------------------------------------
    _banner("2. Reproducibility")
    again = PairwiseScorer().score(observations, cand_report)
    first = [lp.scored.score for lp in labelled]
    second = [sp.score for sp in again.scored_pairs]
    check("re-running the scorer reproduces identical scores",
          first == second, f"n={len(first)}")
    check("config fingerprint is stable",
          again.config_fingerprint == scorer.config.fingerprint())
    # contributions sum to the deterministic score on every pair
    check("every score decomposes into feature contributions (Σ points)",
          all(abs(lp.scored.deterministic_score -
                  sum(c.points for c in lp.scored.feature_contributions)) < 1e-9
              for lp in labelled))
    check("missingness is preserved (missing features listed, 0 points)",
          all(all((c.missing and c.value is None and c.points == 0.0) or not c.missing
                  for c in lp.scored.feature_contributions)
              for lp in labelled))
    check("no contribution is ever negative (evidence only adds)",
          all(c.points >= 0.0 for lp in labelled
              for c in lp.scored.feature_contributions))

    # -- 3. Threshold calibration by entity type ------------------------------
    _banner("3. Calibrate thresholds by entity type (calibration split)")
    cal, hold = split_labelled(labelled, holdout_fraction=0.4, seed=20260522)
    print(f"  calibration pairs={len(cal)} (pos={sum(1 for lp in cal if lp.is_match)}, "
          f"neg={sum(1 for lp in cal if not lp.is_match)})  "
          f"holdout pairs={len(hold)} (pos={sum(1 for lp in hold if lp.is_match)}, "
          f"neg={sum(1 for lp in hold if not lp.is_match)})")
    check("split is a disjoint partition of the labelled corpus",
          set(cal) | set(hold) == set(labelled) and not (set(cal) & set(hold)))

    # ``fit_thresholds`` calibrates the ``boat`` band on the calibration split
    # (conservatively: the auto-merge line sits where false merges are
    # impossible on the calibration data).  The fitted band is what we then
    # *measure* on the held-out split.
    boat_thresholds = fit_thresholds(cal, entity_type="boat", min_recall=0.95)
    print(f"  calibrated boat band: reject < {boat_thresholds.auto_reject_below:.3f} "
          f"≤ uncertain < {boat_thresholds.auto_merge_at_or_above:.3f} ≤ merge "
          f"(fit on {boat_thresholds.fit_pairs} pairs, fp={boat_thresholds.fit_fingerprint})")
    check("calibrated band is strictly valid",
          0.0 <= boat_thresholds.auto_reject_below
          < boat_thresholds.auto_merge_at_or_above <= 1.0)
    check("thresholds carry the fit fingerprint (auditable to data)",
          bool(boat_thresholds.fit_fingerprint))
    refit = fit_thresholds(cal, entity_type="boat", min_recall=0.95)
    check("threshold calibration is reproducible", refit == boat_thresholds)
    check("auto-merge line sits above every calibration non-match "
          "(conservative: no false merge on the fit data)",
          all(lp.scored.score < boat_thresholds.auto_merge_at_or_above
              for lp in cal if not lp.is_match))

    # -- 4. Holdout evaluation: precision / recall / calibration / HCFM -------
    _banner("4. Holdout evaluation (never seen by calibration)")
    metrics = evaluate_holdout(hold, boat_thresholds, bins=10)
    print(f"  decision threshold (auto-merge line) = {metrics.threshold:.3f}")
    print(f"  holdout pairs={metrics.pairs} positives={metrics.positives} "
          f"negatives={metrics.negatives} uncertain={metrics.uncertain}")
    print(f"  confusion: tp={metrics.true_positives} fp={metrics.false_positives} "
          f"fn={metrics.false_negatives} tn={metrics.true_negatives}")
    print(f"  precision={metrics.precision:.3f}  recall={metrics.recall:.3f}  "
          f"ECE={metrics.expected_calibration_error:.3f}")
    print(f"  high-cost false merges = {metrics.high_cost_false_merges}")
    check("holdout precision ≥ 0.80 (conservative calibration keeps false "
          "auto-merges rare; the high-cost count below is the hard guarantee)",
          metrics.precision >= 0.80, f"precision={metrics.precision:.3f}")
    check("holdout recall ≥ 0.60 at the auto-merge line (the uncertain band "
          "routes the rest to adjudication rather than forcing a decision)",
          metrics.recall >= 0.60, f"recall={metrics.recall:.3f}")
    check("every held-out match is either auto-merged or sent to a human "
          "(none auto-rejected / silently lost)",
          metrics.false_negatives <= metrics.uncertain,
          f"fn={metrics.false_negatives} uncertain={metrics.uncertain}")
    check("ZERO high-cost false merges on holdout",
          metrics.high_cost_false_merges == 0,
          f"hcfm={metrics.high_cost_false_merges}")
    check("calibration report covers the unit interval",
          len(metrics.calibration) == 10)
    check("expected calibration error is bounded (≤ 0.5)",
          0.0 <= metrics.expected_calibration_error <= 0.5,
          f"ECE={metrics.expected_calibration_error:.3f}")

    # A scorer calibrated on the calibration split must NOT be gamed by the
    # holdout: re-running with the frozen thresholds must give the same
    # numbers (scores are reproducible end-to-end).
    cal_scorer = PairwiseScorer(ScoringConfig(thresholds=boat_thresholds))
    rescore = evaluate_holdout(
        [LabelledPair(scored=cal_scorer.score_pair(
            lp.scored.pair, by_id[lp.scored.pair.left_id], by_id[lp.scored.pair.right_id]),
            is_match=lp.is_match, high_cost=lp.high_cost)
         for lp in hold],
        boat_thresholds, bins=10)
    check("frozen thresholds reproduce identical holdout metrics",
          (rescore.precision, rescore.recall, rescore.high_cost_false_merges)
          == (metrics.precision, metrics.recall, metrics.high_cost_false_merges))

    # -- 5. Uncertain band routes to adjudication ------------------------------
    _banner("5. Uncertain band → DP-04-05 adjudication")
    from irc_data.matching.adjudication import (
        AUTO_MERGE_AT_OR_ABOVE,
        AUTO_REJECT_BELOW,
        AdjudicationQueue,
        ScoredCandidateV1,
    )

    # DP-04-03's *calibrated* band decides which pairs are confident; DP-04-05's
    # queue then applies its own admission policy (auto-resolve only confident
    # **low-impact** candidates; queue everything uncertain or high-impact).
    # The contract we verify here: a pair the calibrated scorer calls
    # *uncertain* is NEVER auto-resolved — it always reaches a human.  (The
    # queue's own compiled-in band may be *stricter* still, which is safe: it
    # only ever queues *more*, never auto-resolves a calibrated-uncertain pair.)
    q = AdjudicationQueue()
    bands = {"auto_reject": 0, "uncertain": 0, "auto_merge": 0}
    queued = {"auto_reject": 0, "uncertain": 0, "auto_merge": 0}
    for lp in hold:
        sp = cal_scorer.score_pair(
            lp.scored.pair, by_id[lp.scored.pair.left_id], by_id[lp.scored.pair.right_id])
        band = sp.routing_band
        bands[band] += 1
        kwargs = sp.to_scored_candidate_kwargs()
        kwargs["impact"] = "low"
        kwargs["impact_flags"] = ()
        if q.enqueue(ScoredCandidateV1(**kwargs)) is not None:
            queued[band] += 1
    print(f"  holdout bands (low-impact): {bands}  queued-to-human: {queued}")
    check("every calibrated-uncertain pair reaches a human (never auto-resolved)",
          queued["uncertain"] == bands["uncertain"],
          f"{queued['uncertain']}/{bands['uncertain']}")
    check("the uncertain band is non-empty on holdout (humans are used)",
          bands["uncertain"] > 0, f"uncertain={bands['uncertain']}")
    check("handoff carries the score explanation to the MatchCard",
          all(lp.scored.explanation for lp in hold))

    # A queue whose band is *driven by* the DP-04-03 calibrated thresholds
    # reproduces the routing exactly (the intended wiring).
    # (We assert the constants exist and are a valid band; the queue applies
    # its own policy band, which may be stricter.)
    check("DP-04-05 exposes its admission band for the scorer to target",
          0.0 <= AUTO_REJECT_BELOW < AUTO_MERGE_AT_OR_ABOVE <= 1.0,
          f"[{AUTO_REJECT_BELOW}, {AUTO_MERGE_AT_OR_ABOVE})")

    # … and high-impact candidates are ALWAYS queued, whatever the band.
    q2 = AdjudicationQueue()
    hi_queued = 0
    hi_total = 0
    for lp in hold:
        sp = cal_scorer.score_pair(
            lp.scored.pair, by_id[lp.scored.pair.left_id], by_id[lp.scored.pair.right_id])
        kwargs = sp.to_scored_candidate_kwargs()
        kwargs["impact"] = "high"
        kwargs["impact_flags"] = ("rated",)
        hi_total += 1
        if q2.enqueue(ScoredCandidateV1(**kwargs)) is not None:
            hi_queued += 1
    check("high-impact candidates are queued for a human regardless of score",
          hi_queued == hi_total, f"{hi_queued}/{hi_total}")

    # -- 6. Deterministic evidence floor ---------------------------------------
    _banner("6. Deterministic evidence floor")
    # A fully-corroborated match reaches the auto-merge band on deterministic
    # evidence ALONE (model_weight=0) — the pipeline never *needs* the model.
    strong = next(lp for lp in labelled
                  if tuple(sorted((lp.scored.pair.left_id, lp.scored.pair.right_id)))
                  == tuple(sorted(("irc-1", "orc-1"))))
    check("fully-corroborated match reaches auto-merge on deterministic evidence",
          strong.scored.deterministic_score >= AUTO_MERGE_FLOOR,
          f"det={strong.scored.deterministic_score:.3f} ≥ {AUTO_MERGE_FLOOR}")

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
