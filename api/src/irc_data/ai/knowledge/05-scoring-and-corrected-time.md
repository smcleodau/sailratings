# 05 — Scoring and corrected time

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).

## Elapsed and corrected time

- **Elapsed time** is the time a boat takes from her start to her finish.
- **Corrected time** is the elapsed time after the handicap is applied. The boat with the **lowest corrected time wins** the race.
- **Time behind winner** is a boat's corrected time minus the winner's corrected time.

## Time-on-time (ToT)

- In a **ToT** system, corrected time = elapsed time × a rating multiplier. IRC uses ToT with the **TCC** as the multiplier: corrected time = elapsed × TCC.
- The handicap advantage scales with how long the boat is racing, regardless of distance or wind.
- ORC offers time-on-time coefficients too (OSN, Triple Number, TMF).

## Time-on-distance (ToD)

- In a **ToD** system, corrected time = elapsed time − (allowance in seconds per mile × course distance).
- **PHRF** and the ORC **GPH** are ToD ratings, in seconds per mile (PHRF) and seconds per nautical mile (GPH).
- ToD needs a known course distance; the platform stores `course_distance_nm` for results where it is available.

## The seconds-per-hour convention

- One IRC **point** is **0.001 TCC**.
- An hour has 3600 seconds, so one point is worth **3.6 seconds per hour** of elapsed time (0.001 × 3600).
- Worked example: a boat at TCC 1.025 versus one at 1.020 differs by 5 points, which is **18 seconds per hour**.
- A persona must always convert a TCC gap to seconds per hour using this rule and never round it into a different number.

## Performance Curve Scoring

ORC **Performance Curve Scoring** (PCS) corrects a boat's time using the allowances table and the wind strength actually sailed, so it can differ from a single-number correction.

## Series scoring

Series points are normally awarded on the **Low Point System** of the Racing Rules of Sailing (Appendix A): 1 point for a win, 2 for second, and so on, with penalty scores for DNF, DNS and similar. Race results in the platform keep `place`, `class_place`, `fleet_size` and `status`.

## Sources

- RORC Rating Office / UNCL, *IRC Rating Rule* — time-on-time correction — https://www.ircrating.org
- Offshore Racing Congress, *ORC International Rating Rule* and *Performance Curve Scoring* documentation — https://orc.org
- World Sailing, *Racing Rules of Sailing*, Appendix A (Scoring) — https://www.sailing.org
- Platform convention: "point = 0.001 TCC ≈ 3.6 seconds per hour" in the report prompts; `api/src/irc_data/db/models.py` (`RaceResultModel`).
