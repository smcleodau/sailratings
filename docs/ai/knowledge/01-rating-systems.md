# 01 — Rating systems: IRC, ORC and PHRF

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).
> Every persona (report, insights, chat) reads this file through `knowledge.py`. Facts that are platform conventions are labelled **Platform convention**; facts from a rule body are labelled with the source in the Sources list.

## What a handicap rating is

A handicap rating is a number a rating authority assigns to a boat so that boats of different designs can race each other and be ranked on **corrected time**. The three systems the platform deals with are IRC, ORC and PHRF. Their numbers are on **different scales and must never be compared with each other directly**.

## IRC

- IRC is a **time-on-time** handicap system. Its single rating number is the **TCC** (Time Correction Coefficient). Corrected time = elapsed time × TCC.
- IRC is managed jointly by the **RORC Rating Office** (UK) and the **UNCL** (Union Nationale pour la Course au Large, France).
- The IRC rating formula is **confidential**. The Rule is published each year, but the rating formula itself is not. The platform therefore never states how IRC computes a rating; it reports patterns observed empirically in the fleet data.
- A new IRC Rule year takes effect each **1 January**. A certificate carries the rule (cert) year it was issued under.
- TCC is written to three decimal places, for example `1.025`. A **lower TCC** means the rule rates the boat as slower and gives her more time on corrected time. A **higher TCC** means the rule rates the boat as faster.
- Platform convention: one **point** is **0.001 TCC**, which is **3.6 seconds per hour** of racing.

## ORC

- ORC is run by the **Offshore Racing Congress**. Its rating is derived from a **VPP** (Velocity Prediction Program) that predicts boat speed from the measured hull, rig and sails. The ORC rule and VPP documentation are published (open), in contrast to IRC.
- The headline ORC numbers on a certificate are:
  - **GPH** (General Purpose Handicap): a time allowance in **seconds per nautical mile**. A **lower GPH** means a faster boat.
  - **CDL** (Class Division Length): a length-based value used to split boats into classes.
  - **Triple Number** (low / medium / high): three time-on-time coefficients, one for each of three wind-strength bands.
  - **OSN** (Offshore Single Number): a single time-on-time coefficient for offshore racing.
  - **Allowances**: the full table of seconds-per-nautical-mile allowances from the VPP, by wind speed and course type.
- ORC certificates are issued by national ORC member authorities. The platform stores them per `(ref_no, country_id, snapshot_date)`.

## PHRF

- PHRF (Performance Handicap Racing Fleet) is a handicap system used mainly in North America. It is a **time-on-distance** system: the rating is **seconds per mile**, and a **lower number** is a faster boat.
- Ratings are assigned by regional PHRF fleets or committees, not by a central measurement formula, so the same boat can hold different ratings in different regions.
- PHRF is outside the platform's certificate pipeline; it is described here so a persona can recognise it and never conflate it with TCC or GPH.

## Side-by-side

| System | Number | Scale | Method | Lower means |
|---|---|---|---|---|
| IRC | TCC | multiplier near 1.000 | time-on-time | slower rated boat |
| ORC | GPH | seconds per nautical mile | time-on-distance | faster boat |
| ORC | Triple Number / OSN | multiplier | time-on-time | slower rated boat |
| PHRF | PHRF rating | seconds per mile | time-on-distance | faster boat |

## Reviewer flags

- Wind-speed values that define the ORC low/medium/high bands are deliberately not stated here until confirmed from the ORC VPP documentation.
- Exact IRC governance wording (which national authorities issue certificates) is left out on purpose.

## Sources

- RORC Rating Office / UNCL, *IRC Rating Rule* (current year edition) and IRC information pages — https://www.ircrating.org
- Offshore Racing Congress, *ORC International Rating Rule* and *ORC VPP Documentation* — https://orc.org
- US Sailing, *PHRF* handicapping information — https://www.ussailing.org
- Platform convention: `api/src/irc_data/db/models.py` (`ORCCertificate`, `TCCSnapshotModel`) and the "point = 0.001 TCC" convention in the report prompts.
