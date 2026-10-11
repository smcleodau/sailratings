import asyncio

import httpx

from irc_data.scrapers import tcc_listing

HEADER = b"Boat Name,Sail No,Cert No,Issue Date,Cert Year,TCC,Endorsed,Secondary\n"
CSV = HEADER + b"FARTHER BRUIN,BEL1633,123,16/07/2026,2026,0.909,E,\n" * 200


def _patch(monkeypatch, handler):
    monkeypatch.setattr(tcc_listing, "get_http_client",
                        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def no_wait():
        return None

    monkeypatch.setattr(tcc_listing.rate_limiter, "wait", no_wait)


def test_downloads_listing_from_topyacht_direct_url(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})

    _patch(monkeypatch, handler)
    path = asyncio.run(tcc_listing.download_tcc_listing(output_dir=tmp_path))
    assert path is not None and path.read_bytes() == CSV
    assert seen == [tcc_listing.DIRECT_CSV_URL]


def test_rejects_non_listing_body_then_falls_back_to_ircrating_page(tmp_path, monkeypatch):
    seen = []

    def handler(req):
        url = str(req.url)
        seen.append(url)
        if url == tcc_listing.DIRECT_CSV_URL:
            return httpx.Response(200, content=b"<html>terms and conditions</html>" * 50)
        if url == tcc_listing.LISTING_PAGE_URL:
            return httpx.Response(200, text='<a href="https://ircrating.org/wp-content/uploads/2026/10/ClubListing_20261010.csv">x</a>')
        return httpx.Response(200, content=CSV)

    _patch(monkeypatch, handler)
    path = asyncio.run(tcc_listing.download_tcc_listing(output_dir=tmp_path))
    assert path is not None and path.read_bytes() == CSV
    assert seen[0] == tcc_listing.DIRECT_CSV_URL and seen[1] == tcc_listing.LISTING_PAGE_URL


def test_returns_none_when_every_source_fails(tmp_path, monkeypatch):
    _patch(monkeypatch, lambda req: httpx.Response(503))
    assert asyncio.run(tcc_listing.download_tcc_listing(output_dir=tmp_path)) is None
