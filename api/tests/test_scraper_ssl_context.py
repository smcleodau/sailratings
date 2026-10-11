import ssl

from irc_data.scrapers.base import _EXTRA_INTERMEDIATES, get_http_client, scraper_ssl_context


def test_bundled_sectigo_dv_r36_is_loaded():
    assert (_EXTRA_INTERMEDIATES / "sectigo_public_server_auth_ca_dv_r36.pem").exists()
    subjects = [dict(x[0] for x in c["subject"]).get("commonName") for c in scraper_ssl_context().get_ca_certs()]
    assert "Sectigo Public Server Authentication CA DV R36" in subjects


def test_context_still_verifies_hostnames_and_certs():
    ctx = scraper_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_scraper_client_uses_it():
    client = get_http_client()
    try:
        assert client._transport._pool._ssl_context is scraper_ssl_context()
    finally:
        import asyncio
        asyncio.run(client.aclose())
