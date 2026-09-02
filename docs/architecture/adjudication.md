# Human Adjudication Queue & Evidence View (DP-04-05)

> Use humans only where **uncertainty** or **cost** warrants it.
>
> **Code of record:** `api/src/irc_data/matching/adjudication.py`
> (`SCHEMA_VERSION = "adjudication-v1"`, impact model `impact-model-v1`).
> **Builds on:** DP-04-02 (`CandidatePair` — every queued case is explained
> by ≥1 blocking rule), DP-04-03 (score explanation contract), DP-04-04
> (auto-resolution policy and the resolution write contract this module
> shares).
> **Verification:** `api/tests/matching/test_adjudication.py`,
> `api/tests/matching/test_adjudication_api.py`, and the human-runnable
> usability harness `api/scripts/verify_dp_04_05.py`.

---

## 1. Purpose

The automatic pipeline resolves the confident bulk of candidate pairs.
DP-04-05 owns what is left: a **prioritised queue** of uncertain and/or
high-impact candidates, an **evidence view** for each case (the AD-01-04
MatchCard), and the **decision write-path** that adjudication feeds.

Two invariants are enforced on the contracts, not by convention:

1. **Decision writes through the same contract as automatic resolution.**
   `DecisionRequestV1` is the single write contract.  The auto-resolver
   calls `AdjudicationQueue.decide()` with `decided_by="system:resolver"`;
   a human clicking Merge / Keep separate in the MatchCard calls it with
   `decided_by="human:<id>"`.  Both produce the identical
   `ResolutionRecordV1` output contract and share the same status machine.
2. **Double review is required for high-impact merges.**  A `merge` on a
   high-impact case can never be applied by one actor: the first decision
   is a *vote* (`pending_second_review`, case moves to
   `awaiting_second_review`); only a **second, distinct** reviewer repeating
   `merge` applies it, with both reviewers recorded in
   `decided_by_chain`.  A conflicting second decision escalates the case
   instead of silently resolving it.  Voting twice as the same reviewer is
   rejected with `DoubleReviewError` (HTTP 409).

## 2. Queue admission — what reaches a human

`AdjudicationQueue.route(candidate)` classifies every scored candidate:

| score / impact                          | routing                  | who decides |
|-----------------------------------------|--------------------------|-------------|
| `< 0.20`, low impact                    | `auto_reject`            | automatic   |
| `≥ 0.90`, low impact                    | `auto_merge`             | automatic   |
| any score, **high** impact              | `high_impact` / `uncertain_high_impact` | **human** |
| `[0.20, 0.90)`, low/medium impact       | `uncertain`              | **human**   |

Confident low-impact candidates are **never queued** — a human adds no
value there.  Any candidate touching a high-impact flag (`rated`,
`has_results`, `has_certificate` — the `impact-model-v1` high tier) is
always queued, because the *cost* of a wrong merge (corrupted ratings or
race history) warrants a human even at a confident score.

## 3. Prioritisation

```
priority = impact_weight + uncertainty_weight × uncertainty
uncertainty = 1 − |2·score − 1|          (1.0 at score 0.5 — a coin flip)
```

Impact tier weights: high = 2.0, medium = 1.0, low = 0.0.  **Cost is the
primary axis; uncertainty breaks ties** — a high-impact case always sorts
ahead of even a maximally-uncertain low-impact one.

## 4. The evidence view contract: `QueueItemV1`

Each queued case carries everything the MatchCard renders, on one
serialisable contract:

* **side-by-side source evidence** — `left_evidence` / `right_evidence`
  (name, sail number, registry id, design, country, build year, validity,
  source);
* **score explanation** — `score` plus the line-item `score_explanation`
  from the pairwise scorer;
* **downstream impact** — `impact` tier + `impact_flags`;
* **reversible actions** — `actions = (merge, separate, escalate, defer)`;
  every action produces a `ResolutionRecordV1` that
  `reverse_resolution()` can undo;
* **provenance** — the DP-04-02 `CandidatePair` with `rules_fired` /
  `matching_keys`, so every case is auditable back to the blocking rule
  that produced it;
* **double-review state** — `requires_second_review` and the `votes`
  audit trail.

## 5. Reversible actions

`reverse_resolution(resolution_id, decided_by=…)` writes a **new**
`ResolutionRecordV1` whose `undo_of` points at the record being reversed,
marks the original `reversed`, and requeues the case as `pending` so it
can be decided again.  Nothing is deleted; the full audit trail survives.

## 6. API (admin)

```
GET  /v1/admin/adjudication/queue            prioritised open queue
GET  /v1/admin/adjudication/cases[?status=]  all cases
GET  /v1/admin/adjudication/cases/{id}       one case's evidence view + resolution trail
POST /v1/admin/adjudication/enqueue          hand a ScoredCandidateV1 to the queue
POST /v1/admin/adjudication/decide           DecisionRequestV1 (shared write contract)
POST /v1/admin/adjudication/reverse          undo an applied resolution
GET  /v1/admin/adjudication/resolutions      the audit trail
```

All endpoints sit behind the admin bearer credential.  Double-review
violations return HTTP 409; unknown cases 404.

## 7. The MatchCard UI (AD-01-04 certification)

`web/src/app/admin/identity/page.tsx` + `MatchCard.tsx` render the queue
under `/admin/identity` (nav: *Identity*).  The card shows the evidence
view of §4, requires a confirm step before starting a double-review
merge, renders the review chain while a case awaits its second reviewer,
and offers an undo for the last applied decision.  The UI never decides
locally — every click writes through `POST /admin/adjudication/decide`.

## 8. Verification — usability measurement

`api/scripts/verify_dp_04_05.py` adjudicates a **labelled sample**
(5 true duplicates + 5 true distinct boats, in the messy shapes the queue
exists for: sail-prefix drift, case drift, spacing drift, near-identical
names on different hulls) through the *production* decision path —
including double review for high-impact merges — and measures:

* **error rate** — an oracle policy reading the same evidence view the
  MatchCard shows scores **0.00 %** (10/10 correct); a hostile
  merge-everything policy is measured at **50.00 %** error, proving the
  measurement discriminates;
* **time** — per-case and total adjudication time are recorded on every
  `AdjudicationEvent` and aggregated into `UsabilityReportV1`
  (mean seconds/case), with a stable fingerprint so runs are comparable.

Measured evidence (from `verify_dp_04_05.py`): 26/26 checks pass —
admission, prioritisation, evidence view, shared write contract, double
review, reversibility, and the usability measurement.

## 9. Handoff summary

| Consumer | Reads | Guarantee |
|---|---|---|
| Automatic resolver (DP-04-04) | `DecisionRequestV1`, `ResolutionRecordV1` | human and automatic decisions share one write contract and status machine |
| MatchCard UI (AD-01-04) | `QueueItemV1` | side-by-side evidence, score explanation, impact, reversible actions on every case |
| Identity consumers | `ResolutionRecordV1.status == "applied"` | high-impact merges always carry a 2-reviewer `decided_by_chain` |
| Auditors | `AdjudicationStore.to_dicts()`, `/resolutions` | nothing is deleted; every reversal links back via `undo_of` |
| QA / verification | `adjudicate_labelled_sample`, `UsabilityReportV1` | error rate and time over a labelled sample are reproducible |

### Acceptance-criteria traceability

* *"Decision writes through the same contract as automatic resolution"* →
  §1/§5: single `DecisionRequestV1` → `ResolutionRecordV1` path for
  `human:*` and `system:resolver` alike
  (`TestSharedWriteContract`, harness §4).
* *"Double review is required for high-impact merges"* → §1:
  `requires_second_review` + distinct-reviewer guard + conflict
  escalation (`TestDoubleReview`, harness §5, API test
  `test_double_review_enforced_over_http`).
* *"Usability test adjudicates a labelled sample and measures
  error/time"* → §8: `adjudicate_labelled_sample` → `UsabilityReportV1`
  (`TestUsabilityHarness`, harness §7).
