"""Tests for Wayback Machine harvest of historical IRC TCC listings."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from irc_data.scrapers.wayback import harvest_tcc_archives


def _cdx_rows_for(pattern: str, year_timestamps: list[tuple[int, str]]) -> bytes:
    """Build a CDX JSON response. First row is the header (matches fl)."""
    rows: list[list[str]] = [["timestamp", "original"]]
    for year, ts in year_timestamps:
        rows.append([ts, pattern.replace("*", str(year))])
    return json.dumps(rows).encode("utf-8")


@pytest.mark.asyncio
async def test_harvest_tcc_finds_multiple_years(tmp_path, monkeypatch):
    """harvest_tcc_archives should download one CSV per (pattern, snapshot)
    and tag each with the snapshot year."""

    # Eight distinct (pattern, year, ts) snapshots — enough to satisfy the
    # "≥8 years found" assertion in Plan B.
    pattern_timestamps: dict[str, list[tuple[int, str]]] = {
        "https://ircrating.org/wp-content/uploads/*/ClubListing*.csv": [
            (2012, "20120115101010"),
            (2014, "20140215101010"),
            (2016, "20160315101010"),
            (2018, "20180415101010"),
        ],
        "https://ircrating.org/wp-content/uploads/*/tcc-listing*.csv": [
            (2020, "20200515101010"),
            (2022, "20220615101010"),
        ],
        "https://ircrating.org/irc-racing/online-tcc-listings/": [
            (2011, "20110715101010"),
            (2013, "20130815101010"),
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # CDX listing call
        if "/cdx/search/cdx" in request.url.path:
            params = request.url.params
            url_pat = params.get("url", "")
            snaps = pattern_timestamps.get(url_pat, [])
            return httpx.Response(200, content=_cdx_rows_for(url_pat, snaps))
        # Snapshot fetch — anything with /web/{ts}id_/ in the path
        if "id_/" in str(request.url):
            return httpx.Response(200, content=b"CertNo,BoatName\n123,Test\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def fake_get_http_client(**kwargs):
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(
        "irc_data.scrapers.wayback.get_http_client", fake_get_http_client
    )

    # Don't sleep between mocked HTTP calls.
    async def _no_wait(self):  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(
        "irc_data.scrapers.base.RateLimiter.wait", _no_wait
    )

    archives = await harvest_tcc_archives(
        start_year=2010, end_year=2025, out_dir=tmp_path
    )

    years_found = {a["year"] for a in archives}
    assert len(years_found) >= 8, f"only found {years_found}"
    for a in archives:
        assert a["path"].exists(), f"file missing: {a['path']}"
        assert a["path"].read_bytes().startswith(b"CertNo")
