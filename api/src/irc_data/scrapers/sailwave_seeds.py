"""Where UK / Irish clubs publish Sailwave IRC results.

Add a club by appending a :class:`SailwaveSeed`:

* a ``https://sailwave.com/results/<folder>/`` listing (trailing slash) —
  every .htm in it is checked, and upload times make incremental runs cheap;
* a club page that links to Sailwave files;
* a single Sailwave file URL.

Only IRC fleets are stored (see :mod:`irc_data.scrapers.sailwave`), so a
folder that mixes dinghies, ECHO and IRC is fine. ``source`` is the register
slug / ``race_results.source``: series with their own register row (Warsash)
use it, everything else is ``sailwave``.
"""

from __future__ import annotations

from irc_data.scrapers.sailwave import SailwaveSeed

WARSASH = "warsash-spring-series"
SW = "https://sailwave.com/results/"

SAILWAVE_SEEDS: list[SailwaveSeed] = [
    # -- Warsash Spring Series / Spring Championships (Warsash SC) ----------
    # 2025+ files on sailwave.com (linked from warsashsc.org.uk/springseries/).
    SailwaveSeed(SW + "warsashsc/", "Warsash Sailing Club", source=WARSASH,
                 series="Warsash Spring Series", include=r"^(WSS|WSC_Champs)"),
    # 2024 season: Sailwave race pages printed to PDF on the club site (race
    # PDFs have a text layer; the printed series summaries are not parsed).
    # 2023 PDFs are image-only and 2022 is summary-only, so neither is seeded.
    SailwaveSeed("https://warsashsc.org.uk/springseries/2024-2/", "Warsash Sailing Club",
                 source=WARSASH, series="Warsash Spring Series", include=r"^WSS-24-",
                 exclude=r"Summary", pdf=True),
    # 2021 season hosted on the club site.
    SailwaveSeed("https://warsashsc.org.uk/springseries/y2021/", "Warsash Sailing Club",
                 source=WARSASH, series="Warsash Spring Series",
                 include=r"Spring_Championship"),

    # -- Solent / South coast ------------------------------------------------
    SailwaveSeed(SW + "RLymYC/", "Royal Lymington Yacht Club"),
    SailwaveSeed(SW + "chichesteryc/", "Chichester Yacht Club"),
    SailwaveSeed(SW + "hisc/", "Hayling Island Sailing Club"),
    SailwaveSeed(SW + "LangstoneSC/", "Langstone Sailing Club"),
    SailwaveSeed(SW + "PooleYC/", "Poole Yacht Club"),
    SailwaveSeed(SW + "Parkstone/", "Parkstone Yacht Club"),
    SailwaveSeed(SW + "WPNSA/", "Weymouth & Portland National Sailing Academy"),
    SailwaveSeed(SW + "salcombeyc/", "Salcombe Yacht Club"),
    SailwaveSeed(SW + "TorbaySC/", "Torbay Sailing Club"),
    SailwaveSeed(SW + "RTYC/", "Royal Torbay Yacht Club"),
    SailwaveSeed(SW + "Flushing/", "Flushing Sailing Club"),
    # -- East coast / Thames ---------------------------------------------------
    SailwaveSeed(SW + "Medway/", "Medway Yacht Club"),
    SailwaveSeed(SW + "BrightlingseaSC/", "Brightlingsea Sailing Club"),
    # -- Wales ---------------------------------------------------------------
    SailwaveSeed(SW + "CBYCCruiser/", "Cardiff Bay Yacht Club"),
    SailwaveSeed(SW + "Welsh_IRC_Champs_2026.htm", "Welsh IRC Championship"),
    SailwaveSeed(SW + "TV_Welsh_IRC_Champs_2026.htm", "Welsh IRC Championship"),
    SailwaveSeed(SW + "2026 - SCYC AKW IRC1.htm", "South Caernarvonshire Yacht Club"),
    SailwaveSeed(SW + "2026 - SCYC AKW IRC2.htm", "South Caernarvonshire Yacht Club"),
    # -- Northern Ireland / Ireland ------------------------------------------
    SailwaveSeed(SW + "carrickfergussc/", "Carrickfergus Sailing Club", country="GB"),
    SailwaveSeed(SW + "ballyholmyc/", "Ballyholme Yacht Club", country="GB"),
    SailwaveSeed(SW + "RIYC/", "Royal Irish Yacht Club", country="IE"),
    SailwaveSeed(SW + "RCYC/", "Royal Cork Yacht Club", country="IE"),
    SailwaveSeed(SW + "HYC/", "Howth Yacht Club", country="IE"),
    SailwaveSeed(SW + "NYC/", "National Yacht Club", country="IE"),
    SailwaveSeed(SW + "DBSC/", "Dublin Bay Sailing Club", country="IE"),
    SailwaveSeed(SW + "baltimore regatta IRC 2026.htm", "Baltimore Sailing Club", country="IE"),
    SailwaveSeed(SW + "baltimore regatta IRC 2025.htm", "Baltimore Sailing Club", country="IE"),
]


def seeds_for(source: str) -> list[SailwaveSeed]:
    return [s for s in SAILWAVE_SEEDS if s.source == source]
