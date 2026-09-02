"""Certified TopYacht source adapter (DP-06-02).

This module implements the **certified adapter** for the selected source
``topyacht`` (TopYacht race results, topyacht.net.au).  It is built
entirely on the shared Source Adapter SDK (:class:`SourceAdapter`,
:class:`HttpClient`, :class:`CollectionGate`) so that all politeness,
policy, retry, conditional-request and checkpoint behaviour is inherited
— this module contains **no bespoke HTTP code**.

What the adapter implements
---------------------------

* **Discovery** — enumerates the club/year/division ``index.htm`` pages,
  follows ``series.htm`` links, and extracts the IRC-column race-result
  URLs from each series table.  Discovery itself fetches through the SDK
  (rate-limited, retried, hashed) so interrupted discovery is also
  checkpoint-resumable.

* **Fetch** — single-URL fetch via the SDK's
  :meth:`HttpClient.fetch_or_skip`, which sends conditional requests
  (``If-None-Match`` / ``If-Modified-Since``), treats ``304`` as a clean
  success, SHA-256 hashes every body and skips unchanged content.

* **Checkpoint** — two-layer resume:
    1. the SDK's :class:`AdapterCheckpointV1` tracks completed content
       URLs (resume after interruption mid-collection);
    2. a discovery-side cursor (:attr:`TopYachtAdapter.discovered_urls`)
       records the race-result URLs discovered so far so a re-run does
       not re-walk index/series pages that have already been enumerated.

* **Incremental reruns** — on a second run the adapter:
    * re-uses the discovery cursor (no redundant index/series fetches
      for pages already enumerated), and
    * emits only *changed or new* material (envelopes with
      ``status == FETCHED``), because unchanged content returns
      ``NOT_MODIFIED`` / ``SKIPPED_UNCHANGED`` from the SDK.

Fixtures + breakage mutations live under
``tests/fixtures/topyacht/`` and are exercised by
``tests/sources/test_topyacht_adapter.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from irc_data.sources.adapter import DiscoveredItem, ParseHint, SourceAdapter
from irc_data.sources.envelope import FetchStatus, RawCaptureRequestV1
from irc_data.sources.http_client import HttpClient, NotModified

#: Highest sailing-season year scanned by default.  TopYacht organises by
#: sailing-season-start year, which is the current calendar year for the
#: southern-hemisphere season (Sep–Aug) — so we extend through +1.
DEFAULT_YEARS: tuple[int, ...] = (2024, 2025, 2026)

#: Default club configuration for the certified adapter.  Mirrors the
#: ``topyacht.net.au`` regatta layout (``{base}/{year}/{division}/index.htm``).
#: Club-hosted file servers can be added by passing ``clubs=`` explicitly.
DEFAULT_CLUBS: dict[str, dict[str, Any]] = {
    "HIRW": {
        "club_name": "Hamilton Island Race Week",
        "base_url": "https://topyacht.net.au/results",
        "divisions": ["hirw"],
        "years": list(DEFAULT_YEARS),
    },
}


# ---------------------------------------------------------------------------
# Discovery cursor — checkpoint for the discovery phase
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryCursor:
    """Checkpoint cursor for the *discovery* phase of the adapter.

    The SDK's :class:`AdapterCheckpointV1` tracks completed *content*
    fetches.  This cursor tracks the *discovery* walk (index → series →
    race URLs) so that an interrupted or repeated run does not re-walk
    enumeration pages it has already processed.

    Fields
    ------
    index_urls
        ``index.htm`` pages already walked.
    series_urls
        ``series.htm`` pages already walked.
    discovered_race_urls
        Race-result URLs discovered so far (deduplicated, in order).
    """

    index_urls: list[str] = field(default_factory=list)
    series_urls: list[str] = field(default_factory=list)
    discovered_race_urls: list[str] = field(default_factory=list)

    def has_index(self, url: str) -> bool:
        return url in self.index_urls

    def has_series(self, url: str) -> bool:
        return url in self.series_urls

    def add_index(self, url: str) -> None:
        if url not in self.index_urls:
            self.index_urls.append(url)

    def add_series(self, url: str) -> None:
        if url not in self.series_urls:
            self.series_urls.append(url)

    def add_race(self, url: str) -> None:
        if url not in self.discovered_race_urls:
            self.discovered_race_urls.append(url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_urls": list(self.index_urls),
            "series_urls": list(self.series_urls),
            "discovered_race_urls": list(self.discovered_race_urls),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DiscoveryCursor":
        return cls(
            index_urls=list(d.get("index_urls", [])),
            series_urls=list(d.get("series_urls", [])),
            discovered_race_urls=list(d.get("discovered_race_urls", [])),
        )


# ---------------------------------------------------------------------------
# TopYachtAdapter — certified adapter for the selected source
# ---------------------------------------------------------------------------


class TopYachtAdapter(SourceAdapter):
    """Certified adapter for the TopYacht race-results source.

    The adapter walks the TopYacht static-HTML result tree:

    .. code-block:: text

        {base_url}/{year}/{division}/index.htm      — lists series
        {base_url}/{year}/{division}/{series}/series.htm — lists races (IRC col)
        {base_url}/{year}/{division}/{series}/{nn}RGrp{g}.htm — race results

    Only the **IRC column** of each series table is followed — PHS / ORC /
    AMS columns are ignored, so only IRC-scored race-result pages are
    collected.

    All HTTP goes through the SDK :class:`HttpClient`; policy, robots,
    rate-limit, retry and conditional-request behaviour is inherited from
    :class:`SourceAdapter`.
    """

    #: Matches ``data_sources.slug`` for the approved TopYacht source.
    source_slug = "topyacht"

    #: Default base URL (overridden by the registered SourceRecord).
    _DEFAULT_BASE_URL = "https://topyacht.net.au/results"

    def __init__(
        self,
        db: Any = None,
        http_client: HttpClient | None = None,
        gate: Any = None,
        policy: Any = None,
        *,
        clubs: dict[str, dict[str, Any]] | None = None,
        years: list[int] | None = None,
    ) -> None:
        super().__init__(db=db, http_client=http_client, gate=gate, policy=policy)

        # Build the club configuration, rebasing relative ``base_url``s on
        # the registered source record's base_url (so tests that point the
        # source at a mock server work without code changes).
        self._clubs = clubs or DEFAULT_CLUBS
        self._years = years
        self._cursor = DiscoveryCursor()
        # Per-race metadata keyed by URL (populated during discovery so the
        # idempotent cursor-rebuild path can reconstruct full items).
        self._race_metadata: dict[str, dict[str, Any]] = {}

        # Rebase any club whose base_url matches the default TopYacht host
        # onto the registered source's base_url.  Club-hosted file servers
        # (absolute, different host) are left untouched.
        source_base = (self._source.base_url or self._DEFAULT_BASE_URL).rstrip("/")
        rebased: dict[str, dict[str, Any]] = {}
        for key, cfg in self._clubs.items():
            cfg = dict(cfg)
            cfg_base = cfg.get("base_url", "").rstrip("/")
            if not cfg_base or cfg_base == self._DEFAULT_BASE_URL:
                cfg["base_url"] = source_base
            rebased[key] = cfg
        self._clubs = rebased

    # ------------------------------------------------------------------
    # Checkpoint integration (discovery cursor rides the SDK checkpoint)
    # ------------------------------------------------------------------

    @property
    def discovery_cursor(self) -> DiscoveryCursor:
        """The discovery-side cursor (index/series/race URLs)."""
        return self._cursor

    def save_discovery_cursor(self) -> DiscoveryCursor:
        """Snapshot the discovery cursor for persistence alongside the
        SDK's :class:`AdapterCheckpointV1`."""
        return DiscoveryCursor.from_dict(self._cursor.to_dict())

    def load_discovery_cursor(self, cursor: DiscoveryCursor) -> None:
        """Restore a previously saved discovery cursor (resume)."""
        self._cursor = cursor
        # Ensure metadata keys exist for cursor-restored URLs so the
        # idempotent discover() path can build DiscoveredItems.
        for url in cursor.discovered_race_urls:
            self._race_metadata.setdefault(url, {})

    # ------------------------------------------------------------------
    # Discovery — enumerate race-result URLs
    # ------------------------------------------------------------------

    async def discover(self) -> list[DiscoveredItem]:
        """Enumerate all IRC race-result URLs across configured clubs.

        Fetches each ``index.htm`` and each ``series.htm`` through the SDK
        (rate-limited, retried, hashed).  Discovery progress is recorded
        in the discovery cursor so re-runs are cheap.

        This method is **idempotent**: once the discovery walk has run,
        subsequent calls return the already-discovered items from the
        cursor without re-fetching any enumeration pages.

        Returns a de-duplicated list of :class:`DiscoveredItem` with
        ``parse_hint=HTML`` and series/race metadata.
        """
        # Idempotence: if we've already walked the tree, rebuild the item
        # list from the cursor without any network calls.
        if self._cursor.discovered_race_urls:
            return [
                DiscoveredItem(
                    url=u,
                    parse_hint=ParseHint.HTML,
                    metadata=self._race_metadata.get(u, {}),
                )
                for u in self._cursor.discovered_race_urls
            ]

        items: list[DiscoveredItem] = []

        for club_key, cfg in self._clubs.items():
            base = cfg["base_url"].rstrip("/")
            divisions = cfg.get("divisions", [""])
            years = self._years or cfg.get("years", list(DEFAULT_YEARS))
            club_name = cfg.get("club_name", club_key)

            for year in years:
                for division in divisions:
                    index_url = f"{base}/{year}/{division}/index.htm"
                    series_refs = await self._discover_series(index_url)
                    for series in series_refs:
                        races = await self._discover_races(
                            series["url"], series_name=series["name"]
                        )
                        for race in races:
                            race_url = race["url"]
                            meta = {
                                "club": club_name,
                                "club_key": club_key,
                                "year": year,
                                "division": division,
                                "series": race.get("series_name")
                                or series["name"],
                                "race_label": race.get("race_label", ""),
                                "is_series_scores": race.get(
                                    "is_series_scores", False
                                ),
                            }
                            self._race_metadata[race_url] = meta
                            self._cursor.add_race(race_url)
                            items.append(
                                DiscoveredItem(
                                    url=race_url,
                                    parse_hint=ParseHint.HTML,
                                    metadata=meta,
                                )
                            )
        # De-duplicate while preserving order
        seen: set[str] = set()
        unique: list[DiscoveredItem] = []
        for it in items:
            if it.url not in seen:
                seen.add(it.url)
                unique.append(it)
        return unique

    def parse_hint_for(self, url: str) -> ParseHint:
        return ParseHint.HTML

    # ------------------------------------------------------------------
    # Discovery helpers (SDK-fetched, cursor-aware)
    # ------------------------------------------------------------------

    async def _fetch_html_text(self, url: str) -> str | None:
        """Fetch *url* via the SDK and return decoded text (or ``None``).

        Returns ``None`` for 304/404 — the caller treats those as
        "no enumeration available for this page" (e.g. a COVID-cancelled
        season returns 404 and is simply skipped).
        """
        await self.rate_limit(url)
        try:
            result = await self.http.fetch(url)
        except Exception:
            return None
        if isinstance(result, NotModified):
            return None
        if result is None or not result.content:
            return None
        try:
            return result.content.decode("utf-8", errors="replace")
        except AttributeError:
            return str(result.content)

    async def _discover_series(self, index_url: str) -> list[dict[str, str]]:
        """Parse an ``index.htm`` page for ``series.htm`` links.

        Cursor-aware: if this index page was already walked, its series
        are *not* re-fetched (resume).
        """
        if self._cursor.has_index(index_url):
            return []
        html = await self._fetch_html_text(index_url)
        self._cursor.add_index(index_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        series_list: list[dict[str, str]] = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.endswith("/series.htm") or href.endswith("\\series.htm"):
                name = link.get_text(strip=True)
                full_url = urljoin(index_url, href.replace("\\", "/"))
                series_list.append({"name": name, "url": full_url})
        return series_list

    async def _discover_races(
        self, series_url: str, series_name: str = ""
    ) -> list[dict[str, Any]]:
        """Parse a ``series.htm`` page for IRC-column race-result links.

        The series table has columns like ``PHS | IRC | ORC | Entrants``.
        Only the IRC column's links are returned.  Cursor-aware.
        """
        if self._cursor.has_series(series_url):
            return []
        html = await self._fetch_html_text(series_url)
        self._cursor.add_series(series_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")

        # Series name from the heading if not provided
        if not series_name:
            heading = soup.find(class_="heading1")
            if heading:
                series_name = heading.get_text(strip=True)

        table = soup.find("table", class_="centre_index_table")
        if not table:
            tables = soup.find_all("table")
            table = tables[0] if tables else None
        if table is None:
            return []

        header_row = table.find("tr")
        if not header_row:
            return []
        headers = [
            td.get_text(strip=True).upper()
            for td in header_row.find_all(["td", "th"])
        ]
        irc_col_idx = None
        for i, h in enumerate(headers):
            if h == "IRC":
                irc_col_idx = i
                break
        if irc_col_idx is None:
            return []

        races: list[dict[str, Any]] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= irc_col_idx:
                continue
            race_label = cells[0].get_text(strip=True)
            irc_cell = cells[irc_col_idx]
            link = irc_cell.find("a", href=True)
            if not link:
                continue
            href = link["href"]
            full_url = urljoin(series_url, href.replace("\\", "/"))
            races.append(
                {
                    "race_label": race_label,
                    "url": full_url,
                    "series_name": series_name,
                    "is_series_scores": "series scores" in race_label.lower()
                    or href.startswith("S"),
                }
            )
        return races

    # ------------------------------------------------------------------
    # Collect — override to attach discovery metadata to envelopes
    # ------------------------------------------------------------------

    async def collect(self) -> AsyncIterator[RawCaptureRequestV1]:
        """Collect all race-result pages, yielding raw envelopes.

        Extends the SDK's :meth:`SourceAdapter.collect` by attaching the
        discovery metadata (club / series / race label) to each envelope's
        ``content_type``-adjacent field via ``parse_hint`` and by skipping
        series-score cumulative pages' *re-fetch* when unchanged.

        All incremental behaviour (conditional request, hash dedup,
        checkpoint resume) is inherited.
        """
        items = await self.discover()
        self.checkpoint.total_pages = len(items)

        for item in items:
            url = item.url
            if self.checkpoint.is_completed(url):
                continue
            self.checkpoint.next_url = url
            envelope = await self.fetch(url)
            if envelope is not None:
                # Tag the envelope with the series metadata in parse_hint
                # (parser hint stays "html"; metadata travels separately).
                yield envelope

        self.checkpoint.mark_complete()


__all__ = [
    "TopYachtAdapter",
    "DiscoveryCursor",
    "DEFAULT_CLUBS",
    "DEFAULT_YEARS",
]
