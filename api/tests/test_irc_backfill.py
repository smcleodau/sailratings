"""Tests for the multi-strategy IRC historical certificate backfill orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from irc_data.scrapers.irc_backfill import probe_cert


@pytest.mark.asyncio
async def test_probe_cert_finds_via_wayback_when_live_missing(
    tmp_path, monkeypatch
):
    """Live IRC PDF directory returns 404 — fall back to Wayback CDX, then
    download the snapshot. The result must indicate ``source='wayback'``
    and the saved PDF must exist on disk under the configured cache dir.
    """

    saved_pdf = b"%PDF-1.4 fake pdf content"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Order matters — the Wayback snapshot URL embeds the IRC PDF URL,
        # so the snapshot match has to come *before* the ircrating match.
        if "web.archive.org/web/" in url and "id_/" in url:
            return httpx.Response(200, content=saved_pdf)
        if "/cdx/search/cdx" in url:
            body = json.dumps(
                [
                    ["timestamp", "original"],
                    [
                        "20180601120000",
                        "https://ircrating.org/pdfdirectory/"
                        "GBR1234R_TEST_GBR1234.pdf",
                    ],
                ]
            ).encode("utf-8")
            return httpx.Response(200, content=body)
        # Live IRC PDF directory — always 404 in this test.
        if "ircrating.org/pdfdirectory/" in url:
            return httpx.Response(404)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(
        "irc_data.scrapers.irc_backfill.get_http_client", fake_client
    )
    monkeypatch.setattr(
        "irc_data.scrapers.wayback.get_http_client", fake_client
    )

    # Don't sleep between mocked requests.
    async def _no_wait(self):  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(
        "irc_data.scrapers.base.RateLimiter.wait", _no_wait
    )

    cache_dir = tmp_path / "certs"
    monkeypatch.setattr(
        "irc_data.scrapers.irc_backfill.HISTORICAL_CERTS_DIR", cache_dir
    )

    result = await probe_cert(
        cert_number="GBR1234R",
        boat_name="Test",
        sail_number="GBR1234",
        year=2018,
    )

    assert result["source"] == "wayback"
    assert result["status"] == "found"
    pdf_path: Path = result["pdf_path"]
    assert pdf_path.exists()
    assert pdf_path.read_bytes() == saved_pdf


@pytest.mark.asyncio
async def test_probe_cert_finds_live_first(tmp_path, monkeypatch):
    """When the live IRC URL returns a PDF the orchestrator must NOT fall
    through to Wayback."""

    saved_pdf = b"%PDF-1.4 LIVE"

    calls = {"live": 0, "cdx": 0, "snap": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Wayback URLs embed the IRC PDF URL, so match them first.
        if "web.archive.org/web/" in url and "id_/" in url:
            calls["snap"] += 1
            return httpx.Response(404)
        if "/cdx/search/cdx" in url:
            calls["cdx"] += 1
            return httpx.Response(200, content=b"[]")
        if "ircrating.org/pdfdirectory/" in url:
            # Live PDF directory — first variant gets a HEAD then a GET.
            # The HEAD path is what gates Strategy 1; respond with PDF
            # content-type + length so probe_cert proceeds to GET.
            calls["live"] += 1
            return httpx.Response(
                200,
                content=saved_pdf,
                headers={
                    "content-type": "application/pdf",
                    "content-length": str(len(saved_pdf)),
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(
        "irc_data.scrapers.irc_backfill.get_http_client", fake_client
    )
    monkeypatch.setattr(
        "irc_data.scrapers.wayback.get_http_client", fake_client
    )

    async def _no_wait(self):  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(
        "irc_data.scrapers.base.RateLimiter.wait", _no_wait
    )

    cache_dir = tmp_path / "certs"
    monkeypatch.setattr(
        "irc_data.scrapers.irc_backfill.HISTORICAL_CERTS_DIR", cache_dir
    )

    result = await probe_cert(
        cert_number="999",
        boat_name="Live",
        sail_number="GBR999",
        year=2019,
    )

    assert result["source"] == "live"
    assert result["status"] == "found"
    assert result["pdf_path"].read_bytes() == saved_pdf
    assert calls["live"] >= 1
    assert calls["cdx"] == 0
    assert calls["snap"] == 0
