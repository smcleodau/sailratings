"""HalSail seed pages: public /Result/Club|Class|Event pages linked from club sites.

Add a club by appending a :class:`HalSailSeed` with the exact URL the club's
own website links to (note where the link is in ``note``). ``source`` is the
register slug / ``race_results.source`` the rows are stored under.
"""

from __future__ import annotations

from irc_data.scrapers.halsail import HalSailSeed

HAMBLE = "hamble-winter-series"

HALSAIL_SEEDS: list[HalSailSeed] = [
    # Hamble River SC: "Results" on hamblewinterseries.com (home, event pages,
    # noticeboard) links Club/3560; the page holds the current season's HWS
    # and Autumn IRC Championship classes.
    HalSailSeed(
        url="https://www.halsail.com/Result/Club/3560",
        club="Hamble River Sailing Club",
        source=HAMBLE,
        series="Hamble Winter Series",
        note="linked from www.hamblewinterseries.com and hrsc.org.uk/page/hrsc-results",
    ),
    # Island Sailing Club, Cowes (class pages linked from islandsc.org.uk).
    HalSailSeed(
        url="https://halsail.com/Result/Class/33212",
        club="Island Sailing Club",
        series="BESC",
        note="BESC IRC 2026; linked from islandsc.org.uk results",
    ),
    HalSailSeed(
        url="https://halsail.com/Result/Class/33234",
        club="Island Sailing Club",
        series="ISC Inshore Series",
        note="Inshore Series IRC 2026; linked from islandsc.org.uk results",
    ),
    # Royal Yacht Squadron: iframe on rys.org.uk/yachting/results-archive.
    HalSailSeed(
        url="https://archive.halsail.com/Result/Club/1297",
        club="Royal Yacht Squadron",
        note="iframe on rys.org.uk/yachting/results-archive (archive host)",
    ),
]


def seeds_for(source: str) -> list[HalSailSeed]:
    return [s for s in HALSAIL_SEEDS if s.source == source]
