"""Design class seeding and normalization.

Builds the design_classes table from existing IRC and ORC data, then
backfills design_canonical on the boats table.
"""

import re

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from irc_data.db.models import DesignClass

# Known design aliases — maps variant spellings to canonical names.
# Keys are MUST-be uppercased, trimmed, ASCII-ish. The lookup uppercases input.
DESIGN_ALIASES = {
    # Jeanneau Sun Fast
    "SUN FAST 3300": "Sunfast 3300",
    "SUNFAST 3300": "Sunfast 3300",
    "SF3300": "Sunfast 3300",
    "SF 3300": "Sunfast 3300",
    "JEANNEAU SUN FAST 3300": "Sunfast 3300",
    "JEANNEAU SF 3300": "Sunfast 3300",
    "SUN FAST 3600": "Sunfast 3600",
    "SUNFAST 3600": "Sunfast 3600",
    "SUN FAST 3600 2.15": "Sunfast 3600",
    "SUN FAST 3600 2.20 FIN6": "Sunfast 3600",
    "SUN FAST 3200": "Sunfast 3200",
    "SUNFAST 3200": "Sunfast 3200",
    "SUN FAST 3200 1.90": "Sunfast 3200",
    "SUN FAST 3200 R2": "Sunfast 3200",
    "SUN FAST 3200 R2 1.90": "Sunfast 3200",
    # JPK (Jacques Valer / JPK Composites — Brittany, France)
    "JPK 10.10": "JPK 1010",
    "JPK1010": "JPK 1010",
    "JPK 10.30": "JPK 1030",
    "JPK 10.30 2.00": "JPK 1030",
    "JPK 10.30 2.00 WB": "JPK 1030",
    "JPK1030": "JPK 1030",
    "JPK 10.80": "JPK 1080",
    "JPK 10.80 2.15 FIN6": "JPK 1080",
    "JPK1080": "JPK 1080",
    "JPK 11.80": "JPK 1180",
    "JPK 11.80 2.35 FIN6": "JPK 1180",
    "JPK1180": "JPK 1180",
    "JPK 9.60": "JPK 960",
    "JPK 9.60 1.95": "JPK 960",
    "JPK960": "JPK 960",
    "JPK 10.50": "JPK 1050",
    "JPK1050": "JPK 1050",
    # Beneteau First — collapse IRC-suffix variants and (year) variants
    "FIRST 36": "First 36",
    "BENETEAU FIRST 36": "First 36",
    "FIRST 34.7": "First 34.7",
    "FIRST 36.7": "First 36.7",
    "FIRST 36.7 2.20": "First 36.7",
    "BENETEAU FIRST 36.7": "First 36.7",
    "FIRST 40": "First 40",
    "FIRST 40 2.48 T": "First 40",
    "FIRST 40 CR": "First 40 CR",
    "FIRST 40 CR 2.45": "First 40 CR",
    "FIRST 40.7": "First 40.7",
    "FIRST 40.7 2.40": "First 40.7",
    "FIRST 40.7 2.40 DISTINCTION": "First 40.7",
    "FIRST 40.7 2.40 MOD (KEEL)": "First 40.7",
    "BENETEAU FIRST 40.7": "First 40.7",
    "FIRST 45": "First 45",
    "FIRST 45 2.40": "First 45",
    "FIRST 45 2.72 MOD (INT)": "First 45",
    "FIRST 45 F5": "First 45 F5",
    "FIRST 45F5": "First 45 F5",
    "FIRST 27 SE": "First 27 SE",
    "FIRST 27 SE/SEASCAPE 27": "First 27 SE",
    "FIRST 27 SE/SEASCAPE 27 2.00": "First 27 SE",
    "SEASCAPE 27": "First 27 SE",
    "FIRST 35": "First 35",
    "FIRST 35 2.20 (09)": "First 35",
    "FIRST 35 2.30 (09) FIN6": "First 35",
    "FIRST 35 CARBON FK": "First 35",
    "FIRST 30": "First 30",
    "FIRST 30 (2010)": "First 30",
    "FIRST 30 MANUARD": "First 30 Manuard",
    "FIRST 44": "First 44",
    "FIRST 44 (22)": "First 44",
    "FIRST 44 (22) 2.45": "First 44",
    "FIRST 44.7": "First 44.7",
    "FIRST 44.7 2.65": "First 44.7",
    "FIRST 50": "First 50",
    "FIRST 50 (06)": "First 50",
    "FIRST 50 (06) 2.80": "First 50",
    "FIRST 53": "First 53",
    "FIRST 53 (19)": "First 53",
    "FIRST 53 (19) 2.50": "First 53",
    # J Boats
    "J 109": "J/109",
    "J109": "J/109",
    "J/109": "J/109",
    "J 109 2.10": "J/109",
    "J 105": "J/105",
    "J105": "J/105",
    "J/105": "J/105",
    "J 105 1.98": "J/105",
    "J 105-MOD": "J/105",
    "J 111": "J/111",
    "J111": "J/111",
    "J/111": "J/111",
    "J 111 2.20 EU": "J/111",
    "J 112E": "J/112E",
    "J/112E": "J/112E",
    "J 112": "J/112E",
    "J 112 E": "J/112E",
    "J 112 E 2.12": "J/112E",
    "J 112 E 2.23 FIN6": "J/112E",
    "J 99": "J/99",
    "J99": "J/99",
    "J/99": "J/99",
    "J 35": "J/35",
    "J35": "J/35",
    "J/35": "J/35",
    "J 80": "J/80",
    "J/80": "J/80",
    "J80": "J/80",
    "J 92": "J/92",
    "J/92": "J/92",
    "J 92 1.79": "J/92",
    "J 92 S": "J/92S",
    "J/92S": "J/92S",
    "J 92 S 1.90": "J/92S",
    "J 122": "J/122",
    "J/122": "J/122",
    "J 122 2.20": "J/122",
    "J 120": "J/120",
    "J/120": "J/120",
    "J 120 2.13": "J/120",
    "J 121": "J/121",
    "J/121": "J/121",
    "J 121 2.52 FIN6": "J/121",
    "J 121 2.52 FIN6 WB": "J/121",
    "J 133": "J/133",
    "J/133": "J/133",
    "J 133 TPI 2.29": "J/133",
    "J 125": "J/125",
    "J/125": "J/125",
    "J 125 2.48 #01 CUSTOM": "J/125",
    "J 70": "J/70",
    "J/70": "J/70",
    "J 88": "J/88",
    "J/88": "J/88",
    "J 88 1.95": "J/88",
    "J 97": "J/97",
    "J/97": "J/97",
    "J 97 1.92": "J/97",
    # X-Yachts — collapse "X 35", "X-35", "X-35 OD" / similar
    "X-332": "X-332",
    "X 332": "X-332",
    "X 332 1.81": "X-332",
    "X-332 SPORT": "X-332",
    "X-35": "X-35",
    "X 35": "X-35",
    "X 35 2.15": "X-35",
    "X 35 2.15 MOD DRAFT": "X-35",
    "X-35 OD": "X-35",
    "X-362": "X-362",
    "X 362": "X-362",
    "X-362 SPORT": "X-362 Sport",
    "X 362 SPORT": "X-362 Sport",
    "X 362 SPORT 2.24": "X-362 Sport",
    "X-41": "X-41",
    "X 41": "X-41",
    "X 41 2.50": "X-41",
    "X-41 OD": "X-41",
    "X-YACHTS X-41": "X-41",
    "X-37": "X-37",
    "X 37": "X-37",
    "X 37 1.98": "X-37",
    "X-37 (SD)": "X-37",
    "X-37 CUSTOM APPENDAGES": "X-37",
    "X-34": "X-34",
    "X 34": "X-34",
    "X 34 1.90": "X-34",
    "X-302": "X-302",
    "X 302": "X-302",
    "X 302 1.70 PBFE": "X-302",
    "X-302 MK II": "X-302",
    "X-43": "X-43",
    "X 43": "X-43",
    "X-55": "X-55",
    "X 55": "X-55",
    "X 55 3.20": "X-55",
    "X-99": "X-99",
    "X 99": "X-99",
    "X 99 1.79": "X-99",
    "XP 33": "XP 33",
    "XP33": "XP 33",
    "XP 33 1.90": "XP 33",
    "XP 44": "XP 44",
    "XP44": "XP 44",
    "XP 44 2.30": "XP 44",
    "XP 44 SPORT MOD KEEL": "XP 44",
    "XP-55": "XP 55",
    "XP 55": "XP 55",
    "XP55": "XP 55",
    # Farr
    "FARR 280": "Farr 280",
    "FARR280": "Farr 280",
    "FARR 30": "Farr 30",
    "FARR 30 2.15": "Farr 30",
    "FARR 30 OD": "Farr 30",
    "FARR 30M": "Farr 30",
    "FARR 30M CUSTOM": "Farr 30",
    "FARR 30 MOD.": "Farr 30",
    "FARR 36": "Farr 36",
    "FARR 36M": "Farr 36",
    "FARR 36M 2.23": "Farr 36",
    "FARR 38": "Farr 38",
    "FARR 38/11.6": "Farr 11.6",
    "FARR 38/11.6 MOD LH BULB": "Farr 11.6",
    "FARR 11.6": "Farr 11.6",
    "FARR 11.6 ": "Farr 11.6",
    "FARR 31": "Farr 31",
    "FARR 31 (93)": "Farr 31",
    "FARR 31 (93) 1.99 JPN": "Farr 31",
    "FARR 395": "Farr 395",
    "FARR 395 MOD KEEL": "Farr 395",
    "FARR 920": "Farr 920",
    "FARR 920 MOD.": "Farr 920",
    "FARR 43": "Farr 43",
    "FARR 43 CUSTOM": "Farr 43",
    # Dehler
    "DEHLER 30 OD": "Dehler 30 OD",
    "DEHLER 30": "Dehler 30 OD",
    "DEHLER 34": "Dehler 34",
    "DEHLER 34 (06)": "Dehler 34",
    "DEHLER 34 (06) 1.95": "Dehler 34",
    "DEHLER 34 1.76": "Dehler 34",
    "DEHLER 34 JV": "Dehler 34",
    "DEHLER 38": "Dehler 38",
    "DEHLER 38C": "Dehler 38",
    "DEHLER 38C (13)": "Dehler 38",
    "DEHLER 38C (13) 2.20": "Dehler 38",
    "DEHLER 38 (13)": "Dehler 38",
    "DEHLER 38 (13) SQ": "Dehler 38",
    "DEHLER 38 SQ COURSE": "Dehler 38",
    "DEHLER 38CR 1.90": "Dehler 38",
    # Elan
    "ELAN E5": "Elan E5",
    "ELAN E6": "Elan E6",
    "ELAN GT5": "Elan GT5",
    "ELAN GT6": "Elan GT6",
    "ELAN 310": "Elan 310",
    "ELAN 310/320/S3": "Elan 310",
    "ELAN 310/320/S3 2.15": "Elan 310",
    "ELAN S3": "Elan 310",
    "ELAN 350": "Elan 350",
    "ELAN 350/360/E4": "Elan 350",
    "ELAN E4": "Elan 350",
    # Hanse
    "HANSE 320": "Hanse 320",
    "HANSE 345": "Hanse 345",
    "HANSE 370": "Hanse 370",
    "HANSE 388": "Hanse 388",
    "HANSE 400": "Hanse 400",
    "HANSE 415": "Hanse 415",
    "HANSE 418": "Hanse 418",
    "HANSE 430": "Hanse 430",
    "HANSE 445": "Hanse 445",
    "HANSE 458": "Hanse 458",
    # Italia
    "ITALIA 9.98": "Italia 9.98",
    "ITALIA 11.98": "Italia 11.98",
    "ITALIA 1198": "Italia 11.98",
    "ITALIA 1198 2.10": "Italia 11.98",
    "ITALIA 10.98": "Italia 10.98",
    "ITALIA YACHT 10,98": "Italia 10.98",
    "ITALIA 12.98": "Italia 12.98",
    "ITALIA 13.98": "Italia 13.98",
    "ITALIA 14.98": "Italia 14.98",
    # Swan
    "SWAN 36": "Swan 36",
    "SWAN 36 (67)": "Swan 36",
    "SWAN 36 (67) 1.89": "Swan 36",
    "SWAN 36 (88)": "Swan 36",
    "SWAN 36 (88) 2.15": "Swan 36",
    "SWAN 40": "Swan 40",
    "SWAN 40 (92)": "Swan 40",
    "SWAN 40 (92) 2.16": "Swan 40",
    "SWAN 42": "Swan 42",
    "SWAN 44": "Swan 44",
    "SWAN 44 (72)": "Swan 44",
    "SWAN 44 (72) 2.30": "Swan 44",
    "SWAN 44 MKII": "Swan 44",
    "SWAN 45": "Swan 45",
    "SWAN 45 OD": "Swan 45",
    "SWAN 46": "Swan 46",
    "SWAN 53": "Swan 53",
    "SWAN 53 (04)": "Swan 53",
    "SWAN 60": "Swan 60",
    "CLUBSWAN 36": "ClubSwan 36",
    "CLUBSWAN 42": "ClubSwan 42",
    "CLUBSWAN 80": "ClubSwan 80",
    "CLUB SWAN 36": "ClubSwan 36",
    "CLUB SWAN 42": "ClubSwan 42",
    "CLUB SWAN 80": "ClubSwan 80",
    # Archambault
    "A35": "Archambault A35",
    "A 35": "Archambault A35",
    "ARCHAMBAULT A35": "Archambault A35",
    "A13": "Archambault A13",
    "ARCHAMBAULT A13": "Archambault A13",
    "A 31": "Archambault A31",
    "A31": "Archambault A31",
    "ARCHAMBAULT A31": "Archambault A31",
    "A 27": "Archambault A27",
    "A27": "Archambault A27",
    "ARCHAMBAULT A27": "Archambault A27",
    "A 40 RC": "Archambault A40 RC",
    "A40 RC": "Archambault A40 RC",
    "ARCHAMBAULT A40 RC": "Archambault A40 RC",
    "A 40": "Archambault A40",
    "A40": "Archambault A40",
    "ARCHAMBAULT A40": "Archambault A40",
    "ARCHAMBAULT M34": "Archambault M34",
    "M 34": "Archambault M34",
    # GP / Botin / Reichel-Pugh / TP 52 variants
    "GP 42 B&C": "GP 42",
    "GP 42": "GP 42",
    "GP 33": "GP 33",
    "TP 52 R/P": "TP 52",
    "TP 52 3.35 J&V": "TP 52",
    "TP 52 BOTIN": "TP 52",
    "TP 52 B&C": "TP 52",
    "TP 52 FARR": "TP 52",
    "TP 52 J/V": "TP 52",
    "TP 52 JV": "TP 52",
    "TP 52 J/V PAC 52": "TP 52",
    # Maxi 72
    "MAXI 72": "Maxi 72",
    "MAXI 72 J/V": "Maxi 72",
    "MAXI 72 R/P": "Maxi 72",
    "MAXI 72 MILLS": "Maxi 72",
    # Bavaria
    "BAVARIA 46 CR": "Bavaria 46 CR",
    "BAVARIA 46 CR (13)": "Bavaria 46 CR",
    # Oceanis (Beneteau)
    "OCEANIS 423": "Oceanis 423",
    "OCEANIS 423 CLIPPER": "Oceanis 423",
    "OCEANIS 31": "Oceanis 31",
    "OCEANIS 311": "Oceanis 31",
    "OCEANIS 311 CLIPPER": "Oceanis 31",
    "OCEANIS 38": "Oceanis 38",
    "OCEANIS 381": "Oceanis 38",
    "OCEANIS 381 CLIPPER": "Oceanis 38",
    "OCEANIS 38.1": "Oceanis 38",
    "OCEANIS 38.1 PERFORMANCE": "Oceanis 38",
    "OCEANIS 41": "Oceanis 41",
    "OCEANIS 411": "Oceanis 41",
    "OCEANIS 46": "Oceanis 46",
    "OCEANIS 461": "Oceanis 46",
    "OCEANIS 46.1": "Oceanis 46",
    "OCEANIS 46.1 STD": "Oceanis 46",
    # Corby (John Corby — UK)
    "CORBY 29": "Corby 29",
    "CORBY 29 (05)": "Corby 29",
    "CORBY 29 (05) 1.98": "Corby 29",
    "CORBY 33": "Corby 33",
    "CORBY 33 (07)": "Corby 33",
    "CORBY 33 (07) 2.23": "Corby 33",
    "CORBY 36": "Corby 36",
    "CORBY 36 (95)": "Corby 36",
    "CORBY 36 (95) CUSTOM": "Corby 36",
    "CORBY 37": "Corby 37",
    "CORBY 37 CUSTOM": "Corby 37",
    # Pogo
    "POGO 30": "Pogo 30",
    "POGO RC": "Pogo 30",
    # Sun Odyssey
    "SUN ODYSSEY 409/419": "Sun Odyssey 409",
    "SUN ODYSSEY 409": "Sun Odyssey 409",
    # Other genuinely-new designs (>=5 cert support)
    "FAREAST 28 R": "Fareast 28R",
    "FAREAST 28R": "Fareast 28R",
    "SIRENA 38": "Sirena 38",
    # Other multi-name designs
    "ARCONA 380": "Arcona 380",
    "ARCONA 385": "Arcona 380",
    "ARCONA 380/385": "Arcona 380",
    "ARCONA 435": "Arcona 435",
    "ARCONA 435 (22)": "Arcona 435",
    "GRAND SOLEIL 44": "Grand Soleil 44",
    "GRAND SOLEIL 44 (20)": "Grand Soleil 44",
    "GRAND SOLEIL 46": "Grand Soleil 46",
    "GRAND SOLEIL 46 (08)": "Grand Soleil 46",
    "BOTIN FAST 40+": "Fast 40+",
    "FAST 40+": "Fast 40+",
}


# -----------------------------------------------------------------------------
# Designer & builder name normalization
# -----------------------------------------------------------------------------
# The same designers and builders appear in raw cert data with many spellings.
# The modal-text choice during backfill picks whichever variant is most common,
# but if they're all spelt differently the count is split. These maps collapse
# variants BEFORE the modal pick so we choose the canonical spelling.
#
# Both maps use uppercase-trimmed keys. Lookup goes through normalize_designer/
# normalize_builder, which the backfill script imports.

DESIGNER_ALIASES: dict[str, str] = {
    # Daniel Andrieu (Sun Fast 3200/3300/3600 base designer)
    "DANIEL ANDRIEU": "Daniel Andrieu",
    "DANIELANDRIEU": "Daniel Andrieu",
    "ANDRIEU DANIEL": "Daniel Andrieu",
    "ANDRIEU": "Daniel Andrieu",
    "ANDRIEU YACHTS": "Daniel Andrieu",
    "ANDRIEU DESIGN": "Daniel Andrieu",
    "ANDRIEU DANIEL": "Daniel Andrieu",
    # Andrieu + Verdier (Sunfast 3300)
    "DANIEL ANDRIEU / GUILLAUME VERDIER": "Daniel Andrieu / Guillaume Verdier",
    "ANDRIEU/VERDIER": "Daniel Andrieu / Guillaume Verdier",
    "ANDRIEUDANIEL/VERDIERG": "Daniel Andrieu / Guillaume Verdier",
    "VERDIER": "Daniel Andrieu / Guillaume Verdier",
    # Rod Johnstone (J/24, J/80, J/92, J/105, J/109)
    "ROD JOHNSTONE": "Rod Johnstone",
    "RODJOHNSTONE": "Rod Johnstone",
    "R. JOHNSTONE": "Rod Johnstone",
    "JOHNSTONE": "Rod Johnstone",
    "JOHNSTONE ROD": "Rod Johnstone",
    "JOHNSTONE RODNEY": "Rod Johnstone",
    "R. ALAN JOHNSONE": "Rod Johnstone",
    # Alan Johnstone (J/70, J/88, J/99, J/111, J/112E, J/121)
    "ALAN JOHNSTONE": "Alan Johnstone",
    "ALANJOHNSTONE": "Alan Johnstone",
    "JOHNSTON ALAN": "Alan Johnstone",
    "JOHNSTONE ALAN": "Alan Johnstone",
    # Bruce Farr / Farr Yacht Design
    "BRUCE FARR": "Bruce Farr",
    "BRUCEFARR": "Bruce Farr",
    "B. FARR": "Bruce Farr",
    "B.FARR": "Bruce Farr",
    "FARR": "Bruce Farr",
    "FARR BRUCE": "Bruce Farr",
    "FARR YACHT DESIGN": "Bruce Farr",
    "BRUCE FARR / FARR YACHT DESIGN": "Bruce Farr",
    # Niels Jeppesen (X-Yachts)
    "NIELS JEPPESEN": "Niels Jeppesen",
    "NIELSJEPPESEN": "Niels Jeppesen",
    "N. JEPPESEN": "Niels Jeppesen",
    "JEPPESEN": "Niels Jeppesen",
    "JEPPENSON": "Niels Jeppesen",
    "JEPPESON": "Niels Jeppesen",
    "JEPPESEN NIELS": "Niels Jeppesen",
    # Umberto Felci (Dufour 34/350/45 etc, also Italia)
    "UMBERTO FELCI": "Umberto Felci",
    "UMBERTOFELCI": "Umberto Felci",
    "UMBERTOFELICI": "Umberto Felci",
    "UMBERTOFELICI/PATRICR": "Umberto Felci",
    "FELCI": "Umberto Felci",
    "FELCI/ROSEO": "Umberto Felci",
    "FELCI YACHTS": "Umberto Felci",
    "FELCI UMBERTO": "Umberto Felci",
    # Reichel/Pugh (TP 52, Maxi 72)
    "REICHEL PUGH": "Reichel/Pugh",
    "REICHEL-PUGH": "Reichel/Pugh",
    "REICHEL/PUGH": "Reichel/Pugh",
    "REICHEL / PUGH": "Reichel/Pugh",
    # Judel/Vrolijk
    "JUDEL/VROLIJK": "Judel/Vrolijk",
    "JUDEL VROLIJK": "Judel/Vrolijk",
    "JUDEL-VROLIJK": "Judel/Vrolijk",
    "J&V": "Judel/Vrolijk",
    # Botin (TP 52, Fast 40+)
    "BOTIN": "Botin",
    "BOTIN PARTNERS": "Botin",
    "BOTIN & PARTNERS": "Botin",
    "BOTIN-KARKEEK": "Botin & Carkeek",
    "BOTIN & CARKEEK": "Botin & Carkeek",
    "BOTIN-CARKEEK": "Botin & Carkeek",
    # Mark Mills (MAT 12, MAXI 72 Mills)
    "MARK MILLS": "Mark Mills",
    "MARKMILLS": "Mark Mills",
    "MILLS": "Mark Mills",
    "MILLS DESIGN": "Mark Mills",
    # Joubert-Nivelt (Archambault A31/35/40)
    "JOUBERT-NIVELT": "Joubert-Nivelt",
    "JOUBERT NIVELT": "Joubert-Nivelt",
    "JOUBERT/NIVELT": "Joubert-Nivelt",
    # JPK / Jacques Valer
    "JACQUES VALER": "Jacques Valer",
    "VALER JACQUES": "Jacques Valer",
    "VALER": "Jacques Valer",
    # Ker Jason / Jason Ker
    "KER JASON": "Jason Ker",
    "JASON KER": "Jason Ker",
    "KER": "Jason Ker",
    # John Corby
    "JOHN CORBY": "John Corby",
    "CORBY": "John Corby",
    # Frers
    "FRERS": "German Frers",
    "GERMAN FRERS": "German Frers",
    "G. FRERS": "German Frers",
    # Mani Frers
    "MANI FRERS": "Mani Frers",
    # Owen Clarke
    "OWEN CLARKE": "Owen Clarke",
    "OWENCLARKE": "Owen Clarke",
    # Simonis-Voogd
    "SIMONIS-VOOGD": "Simonis-Voogd",
    "SIMONIS VOOGD": "Simonis-Voogd",
    "SIMONIS/VOOGD": "Simonis-Voogd",
    # Cossutti
    "COSSUTTI YACHT DESIGN": "Cossutti Yacht Design",
    "COSSUTTI": "Cossutti Yacht Design",
    # Sam Manuard
    "MANUARD": "Sam Manuard",
    "SAM MANUARD": "Sam Manuard",
    # Biscontini
    "BISCONTINI": "Biscontini",
    "BISCONTINI YACHT D": "Biscontini",
    "BISCONTINI YACHT DESIGN": "Biscontini",
    # Matteo Polli (Grand Soleil 44)
    "MATTEO POLLI": "Matteo Polli",
    "POLLI": "Matteo Polli",
    # Tripp
    "TRIPP": "Bill Tripp",
    "BILL TRIPP": "Bill Tripp",
    # Bakewell-White
    "BAKEWELL/WHITE": "Bakewell-White",
    "BAKEWELL-WHITE": "Bakewell-White",
    "BAKEWELL WHITE": "Bakewell-White",
    # Bob Miller / Ben Lexcen
    "BOB MILLER (BEN LEXCEN)": "Ben Lexcen",
    "BEN LEXCEN": "Ben Lexcen",
    "BOB MILLER": "Ben Lexcen",
    # Carkeek
    "CARKEEK": "Carkeek Design",
    "CARKEEK DESIGN": "Carkeek Design",
    # Generic single-word common in the file
    "ELLIOT": "Greg Elliott",
    "ELLIOTT": "Greg Elliott",
}

BUILDER_ALIASES: dict[str, str] = {
    # Beneteau
    "BENETEAU": "Beneteau",
    "BENETEAU SA": "Beneteau",
    "BENEATEAU": "Beneteau",
    "BENETEAU YACHTS": "Beneteau",
    "BENETEAU FRANCE": "Beneteau",
    # Jeanneau
    "JEANNEAU": "Jeanneau",
    "JEANNEU": "Jeanneau",
    "JEANNEAU FRANCE": "Jeanneau",
    "JEANNEAU SA": "Jeanneau",
    "JEANNEAU BOATS": "Jeanneau",
    "JEANNEAU YACHTS": "Jeanneau",
    "SUNFAST": "Jeanneau",
    # J/Boats
    "J/BOATS": "J/Boats",
    "J BOATS": "J/Boats",
    "JBOATS": "J/Boats",
    "J-BOATS": "J/Boats",
    "J.BOATS": "J/Boats",
    "J.BOATS INTERNATIO": "J/Boats",
    "J BOATS INTERNATIONAL": "J/Boats",
    "J-EUROPE": "J/Boats",
    "J EUROPE": "J/Boats",
    "J/EUROPE": "J/Boats",
    "J COMPOSITES": "J/Composites",
    "J-COMPOSITES": "J/Composites",
    "J/COMPOSITES": "J/Composites",
    "J COMPOSITE": "J/Composites",
    "J-COMPOSITE": "J/Composites",
    "J. COMPOSITE": "J/Composites",
    "J/COMPOSITE": "J/Composites",
    # JPK
    "JPK": "JPK Composites",
    "JPK COMPOSITES": "JPK Composites",
    # X-Yachts
    "X-YACHTS": "X-Yachts",
    "X YACHTS": "X-Yachts",
    "XYACHTS": "X-Yachts",
    "X YACHTS DENMARK": "X-Yachts",
    "X-YACHTS DENMARK": "X-Yachts",
    # Dufour
    "DUFOUR": "Dufour Yachts",
    "DUFOUR YACHTS": "Dufour Yachts",
    "DUFOUR YACHT": "Dufour Yachts",
    # Archambault
    "ARCHAMBAULT": "Archambault",
    # Bavaria
    "BAVARIA": "Bavaria Yachts",
    "BAVARIA YACHTS": "Bavaria Yachts",
    # Hanse
    "HANSE": "Hanse Yachts",
    "HANSE YACHTS": "Hanse Yachts",
    # Italia
    "ITALIA YACHTS": "Italia Yachts",
    "ITALIA YACHT": "Italia Yachts",
    # Elan
    "ELAN": "Elan Yachts",
    "ELAN YACHTS": "Elan Yachts",
    "ELAN MARINE": "Elan Yachts",
    # Nautor / Swan
    "NAUTOR": "Nautor's Swan",
    "NAUTORS": "Nautor's Swan",
    "NAUTOR'S SWAN": "Nautor's Swan",
    "NAUTOR SWAN": "Nautor's Swan",
    # King Marine
    "KING": "King Marine",
    "KING MARINE": "King Marine",
    # McConaghy
    "MCCONAGHY": "McConaghy",
    "MCCONAGHY BOATS": "McConaghy",
    "MCCONAGHY YACHTS": "McConaghy",
    # Cookson
    "COOKSON": "Cookson Boats",
    "COOKSON BOATS": "Cookson Boats",
    # Carroll Marine
    "CARROLL MARINE": "Carroll Marine",
    "CARROLL": "Carroll Marine",
    # Dehler
    "DEHLER": "Dehler Yachts",
    "DEHLER YACHTS": "Dehler Yachts",
    # Sydney Yachts
    "SYDNEY YACHTS": "Sydney Yachts",
    "SYDNEY YACHT": "Sydney Yachts",
    # TPI / Pearson
    "TPI": "TPI Composites",
    "TPI COMPOSITES": "TPI Composites",
    "PEARSON COMPOSITES": "TPI Composites",
    # Cantiere del Pardo (Grand Soleil)
    "CANTIEREDEL PARDO": "Cantiere del Pardo",
    "CANTIERE DEL PARDO": "Cantiere del Pardo",
    "DEL PARDO": "Cantiere del Pardo",
    # Longitud Cero (TP52 / Maxi72)
    "LONGITUD CERO": "Longitud Cero",
    "LONGITUDE ZERO YAC": "Longitud Cero",
    "LONGITUDE ZERO YACHTS": "Longitud Cero",
    # Fareast
    "FAREAST YACHTS": "Fareast Yachts",
    # Advanced Yachts
    "ADVANCED YACHTS": "Advanced Yachts",
    # Cantiere Barberis
    "BARBERIS": "Cantiere Barberis",
    # Composite Creation (Class 40)
    "COMPOSITE CREATION": "Composite Creation",
}


def _norm_key(s: str | None) -> str | None:
    """Trim, collapse internal whitespace, and uppercase for map lookup."""
    if not s:
        return None
    cleaned = re.sub(r"\s+", " ", s.strip())
    if not cleaned:
        return None
    return cleaned.upper()


def normalize_design(raw: str | None) -> str | None:
    """Normalize a design name to its canonical form."""
    if not raw or not raw.strip():
        return None
    clean = raw.strip().upper()
    # Also collapse internal whitespace for the lookup
    collapsed = re.sub(r"\s+", " ", clean)
    if clean in DESIGN_ALIASES:
        return DESIGN_ALIASES[clean]
    if collapsed in DESIGN_ALIASES:
        return DESIGN_ALIASES[collapsed]
    # Return original (trimmed) — preserves whatever casing was there
    return raw.strip()


def normalize_designer(raw: str | None) -> str | None:
    """Normalize a designer name. Returns canonical form if known, else cleaned input.

    Used by the design_classes backfill so that variant spellings of the
    same designer (Andrieu / DanielAndrieu / Daniel Andrieu) collapse to a
    single modal choice.
    """
    if raw is None:
        return None
    raw_str = re.sub(r"\s+", " ", str(raw).strip())
    if not raw_str:
        return None
    key = raw_str.upper()
    if key in DESIGNER_ALIASES:
        return DESIGNER_ALIASES[key]
    # Title-case all-caps inputs; leave mixed-case as-is.
    return raw_str.title() if raw_str.isupper() else raw_str


def normalize_builder(raw: str | None) -> str | None:
    """Normalize a builder name. Same contract as normalize_designer."""
    if raw is None:
        return None
    raw_str = re.sub(r"\s+", " ", str(raw).strip())
    if not raw_str:
        return None
    key = raw_str.upper()
    if key in BUILDER_ALIASES:
        return BUILDER_ALIASES[key]
    return raw_str.title() if raw_str.isupper() else raw_str


def seed_design_classes(engine: Engine) -> dict:
    """Populate design_classes from IRC boats + ORC certificates.

    Returns stats dict.
    """
    stats = {"from_irc": 0, "from_orc": 0, "aliases_added": 0, "total": 0}

    with engine.begin() as conn:
        # Collect designs from IRC boats (only where we have enough boats)
        irc_designs = conn.execute(text("""
            SELECT design, COUNT(*) as cnt,
                   MIN(year_built) as year_first,
                   MAX(year_built) as year_last,
                   AVG(loa) as avg_loa,
                   AVG(beam_max) as avg_beam
            FROM boats
            WHERE design IS NOT NULL AND design != ''
            GROUP BY design
            HAVING COUNT(*) >= 2
            ORDER BY cnt DESC
        """)).fetchall()

        for row in irc_designs:
            canonical = normalize_design(row.design)
            if not canonical:
                continue

            # Collect aliases (original + any known aliases)
            aliases = {row.design.strip()}
            for alias, canon in DESIGN_ALIASES.items():
                if canon == canonical:
                    aliases.add(alias)

            stmt = pg_insert(DesignClass.__table__).values(
                name_canonical=canonical,
                aliases=sorted(aliases),
                year_first=row.year_first,
                year_last=row.year_last,
                nominal_loa=row.avg_loa,
                nominal_beam=row.avg_beam,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="design_classes_name_canonical_key",
                set_={
                    "aliases": stmt.excluded.aliases,
                    "year_first": text("LEAST(design_classes.year_first, EXCLUDED.year_first)"),
                    "year_last": text("GREATEST(design_classes.year_last, EXCLUDED.year_last)"),
                },
            )
            conn.execute(stmt)
            stats["from_irc"] += 1

        # Collect designs from ORC certificates
        orc_classes = conn.execute(text("""
            SELECT class_name, COUNT(*) as cnt,
                   MIN(year_built) as year_first,
                   MAX(year_built) as year_last
            FROM orc_certificates
            WHERE class_name IS NOT NULL AND class_name != ''
            GROUP BY class_name
            HAVING COUNT(*) >= 2
            ORDER BY cnt DESC
        """)).fetchall()

        for row in orc_classes:
            canonical = normalize_design(row.class_name)
            if not canonical:
                continue

            aliases = {row.class_name.strip()}
            for alias, canon in DESIGN_ALIASES.items():
                if canon == canonical:
                    aliases.add(alias)

            stmt = pg_insert(DesignClass.__table__).values(
                name_canonical=canonical,
                aliases=sorted(aliases),
                year_first=row.year_first,
                year_last=row.year_last,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="design_classes_name_canonical_key",
                set_={
                    "year_first": text("LEAST(design_classes.year_first, EXCLUDED.year_first)"),
                    "year_last": text("GREATEST(design_classes.year_last, EXCLUDED.year_last)"),
                },
            )
            conn.execute(stmt)
            stats["from_orc"] += 1

        # Count total
        total = conn.execute(text("SELECT COUNT(*) FROM design_classes")).scalar()
        stats["total"] = total

    return stats


def backfill_design_canonical(engine: Engine) -> int:
    """Set design_canonical on boats from the design_classes alias lookup.

    For each boat where design_canonical is NULL but design is set, look up
    the canonical name. Returns count of boats updated.
    """
    updated = 0
    with engine.begin() as conn:
        # Get all boats needing backfill
        boats = conn.execute(text("""
            SELECT id, design FROM boats
            WHERE design IS NOT NULL AND design != ''
              AND design_canonical IS NULL
        """)).fetchall()

        for boat in boats:
            canonical = normalize_design(boat.design)
            if canonical and canonical != boat.design:
                conn.execute(
                    text("UPDATE boats SET design_canonical = :canonical, updated_at = now() WHERE id = :id"),
                    {"canonical": canonical, "id": boat.id},
                )
                updated += 1
            elif canonical:
                # Even if same as design, set it so we know it's been processed
                conn.execute(
                    text("UPDATE boats SET design_canonical = :canonical, updated_at = now() WHERE id = :id"),
                    {"canonical": canonical, "id": boat.id},
                )
                updated += 1

        # Also try to set design_canonical from ORC class_name for matched boats
        orc_updated = conn.execute(text("""
            UPDATE boats b
            SET design_canonical = oc.class_name,
                updated_at = now()
            FROM (
                SELECT DISTINCT ON (boat_id) boat_id, class_name
                FROM orc_certificates
                WHERE boat_id IS NOT NULL AND class_name IS NOT NULL
                ORDER BY boat_id, snapshot_date DESC
            ) oc
            WHERE b.id = oc.boat_id
              AND b.design_canonical IS NULL
              AND b.design IS NULL
        """)).rowcount
        updated += orc_updated

    return updated
