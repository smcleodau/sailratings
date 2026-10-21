# 02 — IRC certificate anatomy

> Review status: DRAFT — awaiting Stuart's review (Human Gate, card AI-01-01).
> Field names below match the platform's `TCCSnapshotModel` (listing CSV) and `Certificate` (certificate PDF) columns.

## Rating fields (listing CSV)

| Field | Meaning |
|---|---|
| **TCC** | Time Correction Coefficient with spinnaker. Corrected time = elapsed × TCC. |
| **Non-Spi TCC** (`non_spi_tcc`) | The TCC for racing without a spinnaker. |
| **Endorsed** | Marks an endorsed certificate: one whose measurement data has been verified by an approved measurer, as opposed to a standard certificate based on declared data. |
| **Secondary** | Flags a secondary certificate (for example a short-handed certificate). Platform convention: a row is secondary when this column is non-empty or the boat name ends in ` - SEC`. |
| **Cert No / Cert Year / Issue Date** | Certificate identifier, the Rule year it was issued under, and its issue date. |
| **Crew** | The declared crew number on the certificate. |
| **Category** | The design category value printed on the certificate. |

## Hull fields

| Field | Meaning |
|---|---|
| **LH** | Hull length, in metres. |
| **Beam** (`BMAX`) | Maximum beam, in metres. |
| **Draft** | Draft in metres. |
| **DLR** | Displacement-length ratio, stored by the platform as an integer. |
| **Boat weight / Displacement** | Measured weight in kilograms. |
| **BO / SO** | Bow overhang / stern overhang. |
| **WB** | Water ballast, where fitted. |
| **STIX** | Stability index. |
| **AVS** | Angle of vanishing stability, in degrees. |

## Rig and mainsail fields

| Field | Meaning |
|---|---|
| **P** | Mainsail luff length (hoist). |
| **E** | Mainsail foot length (boom). |
| **J** | Base of the fore-triangle. |
| **MUW / MTW / MHW** | Mainsail upper, three-quarter and half widths. |
| **Spreaders** | Number of spreader pairs. |
| **Rig type / Mast material** | Rig configuration and mast construction. |

## Headsail fields

| Field | Meaning |
|---|---|
| **HLU** | Headsail luff length. |
| **HLP** | Headsail luff perpendicular: the distance from the clew to the luff. |
| **HUW / HTW / HHW** | Headsail upper, three-quarter and half widths. |
| **HSA** | Headsail area. |
| **Headsails** | Number of headsails declared. |
| **Flying Headsails** | Number of flying headsails declared (set on a stay not attached to the forestay). |
| **Single Furling Headsail** | Whether a single furling headsail is declared. |

## Spinnaker fields

| Field | Meaning |
|---|---|
| **SPA** | Spinnaker area. |
| **SLU / SLE** | Spinnaker luff / leech lengths. |
| **SFL** | Spinnaker foot length. |
| **SHW** | Spinnaker half width. |
| **Spinnakers** | Number of spinnakers declared. |

Platform convention: the certificate prints one spinnaker block with generic codes. The platform classifies it as **asymmetric** when the luff is longer than the leech (by more than 0.005 m) and **symmetric** otherwise.

## Rating-context fields

| Field | Meaning |
|---|---|
| **Series Date / Age Date** | Dates used by the rule for the boat's series and age allowances. |
| **Racing Area** | The racing-area value printed on the certificate. |
| **SSS Base Value** | The stability-screening base value printed on the certificate; the platform treats it as an opaque integer. |

## Declarations versus measurements

A certificate mixes **measurements** (physical dimensions: LH, beam, draft, displacement, rig and sail dimensions) with **declarations** (what the owner chooses to carry: number of headsails, flying headsails, spinnakers, crew). Declarations can be changed by re-declaring; measurements require re-measurement.

## Reviewer flags

- STL, SPL, FL, LWP and the SSS base value are stored by the platform but their precise IRC definitions are not asserted here; confirm against the IRC Rule before adding them.

## Sources

- RORC Rating Office / UNCL, *IRC Rating Rule*, certificate and measurement definitions — https://www.ircrating.org
- Platform parsers: `api/src/irc_data/parsers/tcc_csv.py` (listing column map) and `api/src/irc_data/parsers/certificate_pdf.py` (certificate layout).
- Platform schema: `api/src/irc_data/db/models.py` (`TCCSnapshotModel`, `Certificate`).
