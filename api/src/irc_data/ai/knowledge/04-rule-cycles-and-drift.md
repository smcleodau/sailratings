# 04 — Rule cycles and drift

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).

## Rule cycles

- **IRC**: a new Rule year takes effect each **1 January**. Each certificate carries the rule (cert) year it was issued under, so two certificates for the same boat from different years may be rated under slightly different rules.
- **ORC**: the VPP and rule are revised periodically; ORC certificates carry the data of the year they were issued. The platform stores ORC certificates as dated **snapshots** per country.
- A certificate is a **snapshot in time**. A boat's rating history is the sequence of its certificates.

## Why a rating moves

A boat's TCC can change between certificates for any of these reasons, and a persona must say which one the data supports:

1. **Re-measurement**: the boat or sails were measured again.
2. **Declaration change**: the owner changed headsails, flying headsails, spinnakers or crew.
3. **Rule change**: the rule year changed.
4. **Modification**: displacement, draft, rig or sail dimensions were altered.
5. **Age or series allowances**: dates on the certificate roll forward.

## Drift

**Drift** is a change in the fleet-wide relationship between measurements and TCC over time, as seen in the platform's regressions. In the platform:

- Drift is computed per design over a window of years from regression coefficients (`get_design_drift`).
- The fleet-wide `mean_drift` is the average TCC change; drift counts as **observed** only when its absolute value exceeds **0.001 TCC** (one point).
- A dimension is reported as **affected** when its regression coefficient changes by more than **0.2**.

Drift is **empirical**. Because the IRC formula is confidential, a persona must say "the fleet data shows a shift" and must not say "the rule changed the weighting of X".

## Sources

- RORC Rating Office / UNCL, *IRC Rating Rule* (annual editions) — https://www.ircrating.org
- Offshore Racing Congress, *ORC VPP Documentation* — https://orc.org
- Platform code: `api/src/irc_data/api/services/report/facts_builders.py` (drift thresholds), `api/src/irc_data/db/models.py` (`TCCSnapshotModel.cert_year`, `ORCSnapshot`).
