# 03 — ORC certificate anatomy

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).
> Field names match the platform's `ORCCertificate` model.

## Identity

| Field | Meaning |
|---|---|
| **ref_no** | The ORC certificate reference number. |
| **country_id** | The issuing country (ORC member authority). |
| **yacht_name / sail_no / owner_name** | Boat name, sail number and owner on the certificate. |
| **class_name / builder / designer / year_built** | Design class, builder, designer and year of build. |

## Headline ratings

| Field | Meaning |
|---|---|
| **GPH** | General Purpose Handicap, in **seconds per nautical mile**. Lower is faster. |
| **CDL** | Class Division Length, used to divide boats into classes. |
| **Triple Number** (`triple_low`, `triple_med`, `triple_high`) | Three time-on-time coefficients for low, medium and high wind strength. |
| **OSN** | Offshore Single Number: one time-on-time coefficient for offshore racing. |
| **TMF offshore / inshore** | Time Multiplication Factors for offshore and inshore scoring. |

## Allowances

**Allowances** are the VPP's time allowances in seconds per nautical mile, tabulated by true wind speed and by point of sailing (beat, reach, run) for the two standard course types, **CR** (circular random) and **WL** (windward-leeward). The platform stores this table as JSON in `allowances`. Allowances feed ORC **Performance Curve Scoring** (PCS), which corrects a boat's time using the wind strength actually sailed rather than a single number.

## Dimensions and performance fields

| Field | Meaning |
|---|---|
| **LOA** | Length overall. |
| **Displacement / dspl_sailing** | Measured displacement and sailing displacement, in kilograms. |
| **Draft** | Draft in metres. |
| **sail_area_upwind / sail_area_downwind** | Sail area used in the upwind and downwind VPP runs. |
| **stability_index** | Stability index. |
| **dynamic_allowance** | The dynamic allowance value reported on the certificate. |
| **WSS** | Wetted surface. |
| **IMSL, MB, APHD, APHT** | Length, maximum beam and appendage-related values carried on the certificate; the platform stores them as reported. |

## Rule

- The ORC rating is the output of the VPP applied to measured data. The ORC rule and VPP documentation are public, so for ORC boats a persona may describe mechanisms by citing the ORC documentation; it must not invent mechanisms beyond it.
- Many ORC certificates in the platform data arrive with the summary fields filled and the allowances still missing; "no GPH on file" means the detail has not been fetched yet, not that the boat has no rating.

## Reviewer flags

- IMSL, MB, APHD and APHT descriptions are deliberately generic until confirmed against the ORC VPP documentation.
- The wind speeds behind the low/medium/high Triple Number bands are not stated until confirmed.

## Sources

- Offshore Racing Congress, *ORC International Rating Rule* and *ORC VPP Documentation* — https://orc.org
- ORC, *Performance Curve Scoring* documentation — https://orc.org
- Platform schema: `api/src/irc_data/db/models.py` (`ORCCertificate`).
