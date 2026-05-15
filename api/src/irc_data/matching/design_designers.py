"""Curated mapping of design class -> designer (and optional builder).

This is a hand-maintained dict applied via the `seed-design-designers` CLI
command to populate `design_classes.designer` / `design_classes.builder`,
which the `backfill-boat-identity` command then propagates to `boats`.

Rules:
- Keys must match `design_classes.name_canonical` exactly (case-sensitive).
- If a design has multiple plausible designers (e.g. TP 52, GP 42, Maxi 72),
  leave it out. Wrong data is worse than no data.
- Where two normalised variants exist for the same hull, include both.
"""

from __future__ import annotations

# (designer, builder) — builder may be "" if unknown / multi-builder OD.
DESIGN_DESIGNERS: dict[str, tuple[str, str]] = {
    # --- Jeanneau Sun Fast / Sun Odyssey ---
    "Sunfast 3300": ("Daniel Andrieu / Guillaume Verdier", "Jeanneau"),
    "Sunfast 3200": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3200": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3200 1.90": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3200 R2 1.90": ("Daniel Andrieu", "Jeanneau"),
    "Sunfast 3600": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3600": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3600 2.15": ("Daniel Andrieu", "Jeanneau"),
    "SUN FAST 3600 2.20 Fin6": ("Daniel Andrieu", "Jeanneau"),
    "Jeanneau SO 349": ("Marc Lombard", "Jeanneau"),
    "Jeanneau SO 409": ("Philippe Briand", "Jeanneau"),

    # --- J/Boats (designed by Rod Johnstone & family) ---
    "J Boats J24": ("Rod Johnstone", "J/Boats"),
    "J 24": ("Rod Johnstone", "J/Boats"),
    "J 24 OD": ("Rod Johnstone", "J/Boats"),
    "J Boats J/70": ("Alan Johnstone", "J/Boats"),
    "J 70 OD": ("Alan Johnstone", "J/Boats"),
    "J 80": ("Rod Johnstone", "J/Boats"),
    "J 80 OD": ("Rod Johnstone", "J/Boats"),
    "J 88 1.95": ("Alan Johnstone", "J/Boats"),
    "J 92 1.79": ("Rod Johnstone", "J/Boats"),
    "J 92 S 1.90": ("Rod Johnstone", "J/Boats"),
    "J 97 1.92": ("Alan Johnstone", "J/Boats"),
    "J 99": ("Alan Johnstone", "J/Boats"),
    "J/99": ("Alan Johnstone", "J/Boats"),
    "J 105": ("Rod Johnstone", "J/Boats"),
    "J 105 1.98": ("Rod Johnstone", "J/Boats"),
    "J 109": ("Rod Johnstone", "J/Boats"),
    "J 109 2.10": ("Rod Johnstone", "J/Boats"),
    "J/109": ("Rod Johnstone", "J/Boats"),
    "J 111": ("Alan Johnstone", "J/Boats"),
    "J 111 2.20 EU": ("Alan Johnstone", "J/Boats"),
    "J 112 E 2.12": ("Alan Johnstone", "J/Boats"),
    "J 112 E 2.23 Fin6": ("Alan Johnstone", "J/Boats"),
    "J 120": ("Rod Johnstone", "J/Boats"),
    "J 121 2.52 Fin6 WB": ("Alan Johnstone", "J/Boats"),
    "J 122": ("Rod Johnstone", "J/Boats"),
    "J 122 2.20": ("Rod Johnstone", "J/Boats"),
    "J 22 OD": ("Rod Johnstone", "J/Boats"),

    # --- Bruce Farr / Farr Yacht Design ---
    "Farr 40": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 40 OD": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 40 2.60": ("Bruce Farr / Farr Yacht Design", ""),
    "Farr 40 IOR": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 30 OD": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 30 2.15": ("Bruce Farr / Farr Yacht Design", ""),
    "Farr 11.6": ("Bruce Farr / Farr Yacht Design", ""),
    "Farr 1020": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 31": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 36": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 36M 2.23": ("Bruce Farr / Farr Yacht Design", ""),
    "FARR 727 1.40": ("Bruce Farr / Farr Yacht Design", ""),

    # --- Beneteau First (Berret-Racoupeau / Finot-Conq / others) ---
    "First 40": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 40": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "Beneteau First 40": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 40 2.48 T": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "First 40.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 40.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 40.7 2.40": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 44.7 2.65": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 47.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "Beneteau First 47.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 36.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "First 36.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 36.7 2.20": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 35 2.20 (09)": ("Finot-Conq", "Beneteau"),
    "FIRST 35": ("Finot-Conq", "Beneteau"),
    "Beneteau First 35": ("Andre Mauric", "Beneteau"),
    "First 34.7": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "FIRST 31.7 1.90": ("Groupe Finot", "Beneteau"),
    "FIRST 33.7": ("Groupe Finot", "Beneteau"),
    "FIRST 45": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "First 45": ("Bruce Farr / Farr Yacht Design", "Beneteau"),
    "Beneteau First 50": ("Bruce Farr / Farr Yacht Design", "Beneteau"),

    # --- Beneteau Oceanis (cruiser series) ---
    "Beneteau Oceanis 38": ("Finot-Conq / Nauta", "Beneteau"),
    "Beneteau Oceanis 38.1": ("Finot-Conq / Nauta", "Beneteau"),
    "Beneteau Oceanis 40.1": ("Marc Lombard", "Beneteau"),
    "Beneteau Oceanis 41": ("Finot-Conq / Nauta", "Beneteau"),
    "Beneteau Oceanis 45": ("Finot-Conq / Nauta", "Beneteau"),
    "Beneteau Sense 50": ("Berret-Racoupeau", "Beneteau"),

    # --- Mark Mills designs ---
    "CAPE 31": ("Mark Mills", ""),
    "Cape  31": ("Mark Mills", ""),
    "CAPE 31 UK-IRL Class Standard": ("Mark Mills", ""),
    "Mills 39": ("Mark Mills", ""),
    "Mills 41": ("Mark Mills", ""),
    "Mills 45": ("Mark Mills", ""),
    "LANDMARK 43": ("Mark Mills", ""),

    # --- Sydney Yachts / Murray / Adams / Hick / Inglis (AU designers) ---
    "Sydney 38": ("Iain Murray / Andy Dovell", "Sydney Yachts"),
    "SYDNEY 38": ("Iain Murray / Andy Dovell", "Sydney Yachts"),
    "SYDNEY 38 2.69": ("Iain Murray / Andy Dovell", "Sydney Yachts"),
    "Sydney 36": ("Sydney Yachts", "Sydney Yachts"),
    "Sydney 36Cr": ("Sydney Yachts", "Sydney Yachts"),
    "SYDNEY 36 CR": ("Sydney Yachts", "Sydney Yachts"),
    "Sydney 32": ("Sydney Yachts", "Sydney Yachts"),
    "BASHFORD (SYDNEY) 36": ("Iain Murray / Andy Dovell", "Bashford"),
    "Adams 10": ("Joe Adams", ""),
    "ADAMS 10": ("Joe Adams", ""),
    "ADAMS 10 CR": ("Joe Adams", ""),
    "ADAMS 10 R": ("Joe Adams", ""),
    "Adams 10 (modified)": ("Joe Adams", ""),
    "Adams 10.6": ("Joe Adams", ""),
    "Adams 31": ("Joe Adams", ""),
    "HICK 35": ("Murray Hick", ""),
    "HICK 39": ("Murray Hick", ""),
    "HICK 40 #1": ("Murray Hick", ""),
    "Murray 33": ("Iain Murray", ""),
    "Inglis 12": ("John Inglis", ""),

    # --- Swarbrick / Northshore / Cavalier / Cole / Bull (AU builders) ---
    "Swarbrick S80": ("John Swarbrick", "Swarbrick"),
    "Northshore 27": ("Northshore Yachts", "Northshore"),
    "Northshore 30": ("Northshore Yachts", "Northshore"),
    "Northshore 31": ("Northshore Yachts", "Northshore"),
    "Northshore 33": ("Northshore Yachts", "Northshore"),
    "Northshore 38": ("Northshore Yachts", "Northshore"),
    "NORTHSHORE 38": ("Northshore Yachts", "Northshore"),
    "Northshore 369": ("Northshore Yachts", "Northshore"),
    "Northshore 370": ("Northshore Yachts", "Northshore"),
    "Cavalier 28": ("Laurie Davidson", "Cavalier Yachts"),
    "Cavalier 37": ("Laurie Davidson", "Cavalier Yachts"),

    # --- Etchells / Dragon / Folkboat / Yngling / Sonar / 5.5 (one-designs) ---
    "Etchells": ("E. W. Etchells", ""),
    "ETCHELLS 22": ("E. W. Etchells", ""),
    "M11 Etchells": ("E. W. Etchells", ""),
    "Heritage Etchells": ("E. W. Etchells", ""),
    "Heritage  Etchells": ("E. W. Etchells", ""),
    "Pacesetter Etchells": ("E. W. Etchells", ""),
    "Pacesetter": ("E. W. Etchells", ""),
    "Pamcraft  Etchells": ("E. W. Etchells", ""),
    "Cruising Etchells": ("E. W. Etchells", ""),
    "Modified Etchells": ("E. W. Etchells", ""),
    "Ontario Etchells": ("E. W. Etchells", ""),
    "Dragon": ("Johan Anker", ""),
    "Folkboat": ("Tord Sunden", ""),
    "Nordic Folkboat": ("Tord Sunden", ""),
    "Yngling": ("Jan Herman Linge", ""),
    "Sonar": ("Bruce Kirby", ""),
    "SONAR OD": ("Bruce Kirby", ""),
    "Laser": ("Bruce Kirby", ""),
    "Finn": ("Rickard Sarby", ""),
    "International 5.5": ("various", ""),
    "International 5.5m": ("various", ""),
    "Evolution International 5.5": ("various", ""),
    "International 5.5 Modern": ("various", ""),

    # --- Elliott designs (Greg Elliott) ---
    "Elliott 6": ("Greg Elliott", ""),
    "Elliott 7": ("Greg Elliott", ""),
    "ELLIOTT 7": ("Greg Elliott", ""),

    # --- Sigma / Contessa / Impala (UK production) ---
    "SIGMA 33": ("David Thomas", "Marine Projects"),
    "SIGMA 33 OD": ("David Thomas", "Marine Projects"),
    "SIGMA 38": ("David Thomas", "Marine Projects"),
    "SIGMA 38 OOD": ("David Thomas", "Marine Projects"),
    "CONTESSA 32": ("David Sadler", "Jeremy Rogers"),
    "IMPALA 28 I/B": ("David Thomas", ""),
    "IMPALA 28 OD O/B": ("David Thomas", ""),

    # --- Archambault (Joubert-Nivelt) ---
    "A 31 1.90": ("Joubert-Nivelt", "Archambault"),
    "A 35 2.10": ("Joubert-Nivelt", "Archambault"),
    "ARCHAMBAULT A31": ("Joubert-Nivelt", "Archambault"),
    "ARCHAMBAULT A35": ("Joubert-Nivelt", "Archambault"),
    "Archambault A35": ("Joubert-Nivelt", "Archambault"),
    "A 40 RC 2.48": ("Joubert-Nivelt", "Archambault"),

    # --- X-Yachts (Niels Jeppesen) ---
    "X 332 1.81": ("Niels Jeppesen", "X-Yachts"),
    "X-332": ("Niels Jeppesen", "X-Yachts"),
    "X 35 2.15": ("Niels Jeppesen", "X-Yachts"),
    "X-35 OD": ("Niels Jeppesen", "X-Yachts"),
    "X 37 1.98": ("Niels Jeppesen", "X-Yachts"),
    "X-41 OD": ("Niels Jeppesen", "X-Yachts"),
    "XP-33": ("Niels Jeppesen", "X-Yachts"),
    "XP-44": ("Niels Jeppesen", "X-Yachts"),

    # --- Elan (Rob Humphreys) ---
    "ELAN 31 1.85": ("Rob Humphreys", "Elan"),
    "ELAN 333": ("Rob Humphreys", "Elan"),
    "ELAN 333 1.90": ("Rob Humphreys", "Elan"),
    "Elan 37": ("Rob Humphreys", "Elan"),

    # --- Melges (Reichel/Pugh) ---
    "Melges 24": ("Reichel/Pugh", "Melges"),
    "MELGES 24": ("Reichel/Pugh", "Melges"),
    "MELGES 24 OD": ("Reichel/Pugh", "Melges"),
    "Melges 32": ("Reichel/Pugh", "Melges"),
    "MELGES 32": ("Reichel/Pugh", "Melges"),
    "MELGES 32 L/L": ("Reichel/Pugh", "Melges"),

    # --- JPK (Jacques Valer) ---
    "JPK 10.30": ("Jacques Valer", "JPK Composites"),
    "JPK 1030": ("Jacques Valer", "JPK Composites"),
    "JPK 10.30 2.00": ("Jacques Valer", "JPK Composites"),
    "JPK 10.30 2.00 WB": ("Jacques Valer", "JPK Composites"),
    "JPK 11.80": ("Jacques Valer", "JPK Composites"),
    "JPK 11.80 2.35 Fin6": ("Jacques Valer", "JPK Composites"),

    # --- Hanse (Judel/Vrolijk) ---
    "Hanse 400": ("judel/vrolijk", "Hanse"),
    "Hanse 445": ("judel/vrolijk", "Hanse"),
    "Hanse 455": ("judel/vrolijk", "Hanse"),
    "Hanse 470e": ("judel/vrolijk", "Hanse"),
    "JUDEL/VROLIJK 9.6 CR": ("judel/vrolijk", ""),
    "JUDEL/VROLIJK 35 CR": ("judel/vrolijk", ""),

    # --- Mumm / Corby / Ker / Carkeek / Cookson (modern racers) ---
    "MUMM 36": ("Bruce Farr / Farr Yacht Design", ""),
    "CORBY 25 1.80": ("John Corby", ""),
    "CORBY 29 (05) 1.98": ("John Corby", ""),
    "KER 40": ("Jason Ker", ""),
    "CARKEEK 40 Mk2 GP MOD LH": ("Shaun Carkeek", ""),
    "COOKSON 50": ("Reichel/Pugh", "Cookson Boats"),
    "COOKSON 12m 2.57": ("", "Cookson Boats"),

    # --- Dehler / Dufour / Comet (mid-size euro) ---
    "DEHLER 34 1.76": ("judel/vrolijk", "Dehler"),
    "DUFOUR 40": ("Umberto Felci", "Dufour"),
    "Dufour 40": ("Umberto Felci", "Dufour"),
    "DUFOUR 40 2.10": ("Umberto Felci", "Dufour"),
    "COMET 38 S": ("Vallicelli", "Comar"),

    # --- C&C / S&S / Swan (classics) ---
    "C&C 115": ("C&C Design", "C&C Yachts"),
    "S&S 34": ("Sparkman & Stephens", ""),
    "SWAN 42 CLUB": ("German Frers", "Nautor's Swan"),
    "SWAN 48": ("Sparkman & Stephens", "Nautor's Swan"),
    "MAXI 72": ("various", ""),

    # --- ClubSwan / Italia / Grand Soleil / NEO / IC37 / Pogo ---
    "Italia 11.98": ("Cossutti Yacht Design", "Italia Yachts"),
    "ITALIA 1198 2.10": ("Cossutti Yacht Design", "Italia Yachts"),
    "Grand Soleil 40": ("Marco Lostuzzi / Nauta", "Cantiere del Pardo"),
    "NEO 430 ROMA 2.90": ("Paolo Semeraro", "NEO Yachts"),
    "IC 37": ("Mark Mills", "Westerly Marine"),
    "POGO 36": ("Finot-Conq", "Pogo Structures"),

    # --- Sun Odyssey / Bavaria etc cruisers ---
    "Bavaria 38": ("Farr Yacht Design", "Bavaria"),
    "Bavaria 41": ("Farr Yacht Design", "Bavaria"),
    "Bavaria 42": ("J&J Design", "Bavaria"),

    # --- Old IRC small / SB20 / Flying Tiger / VX One / RS / Fareast ---
    "SB20 OD": ("Tony Castro", ""),
    "Flying Tiger": ("Robert Perry", "Fareast Yachts"),
    "VX One": ("Brian Bennett / Reichel/Pugh", "Ovington"),
    "Mackay VX One": ("Brian Bennett / Reichel/Pugh", "Mackay Boats"),
    "RS Aero 9": ("Jo Richards", "RS Sailing"),
    "FAREAST 28 R 1.70": ("Simonis-Voogd", "Fareast Yachts"),
    "Fareast 28R": ("Simonis-Voogd", "Fareast Yachts"),
    "Far East": ("Simonis-Voogd", "Fareast Yachts"),

    # --- Reichel/Pugh and Volvo Open 70 (multi-designer, mostly named after design) ---
    "MC 38": ("Reichel/Pugh", "McConaghy"),
    "DK 46": ("Reichel/Pugh", ""),

    # --- One-off / small AU classes ---
    "Young 88": ("Jim Young", ""),
    "YOUNG 88": ("Jim Young", ""),
    "Endeavour 26": ("Don MacIntosh", ""),
    "Hood 23": ("Ted Hood", ""),
    "Hood 25": ("Ted Hood", ""),
    "REFLEX 38 2.30": ("Phil Morrison", "Reflex Yachts"),
    "MUSTANG Mk2 1.71": ("Bob Miller (Ben Lexcen)", ""),
    "11m One Design": ("Bruce Farr / Farr Yacht Design", ""),
    "11M OD": ("Bruce Farr / Farr Yacht Design", ""),
    "Couta": ("traditional", ""),
}
