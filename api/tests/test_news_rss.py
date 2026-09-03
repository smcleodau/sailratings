"""Tests for the RSS-based sailing-news path (OPS-02-06).

The acceptance criterion: **Firecrawl calls to news domains = 0**. These
tests pin the two halves of that guarantee:

1. The RSS path (``parse_feed`` / ``html_to_text``) ingests news without any
   Firecrawl dependency.
2. The deprecated Firecrawl path (``scrape_news_source``) refuses to run
   unless the operator sets ``ALLOW_FIRECRAWL_NEWS=1``.

No network, Firecrawl key, or Gemini key is required.
"""

from __future__ import annotations

import pytest

from irc_data.scrapers import news


RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Scuttlebutt Sailing News</title>
    <item>
      <title>Rampage 88 wins offshore classic</title>
      <link>https://www.sailingscuttlebutt.com/2026/09/01/rampage-wins/</link>
      <pubDate>Tue, 01 Sep 2026 10:00:00 +0000</pubDate>
      <description>Rampage 88 took line honours.</description>
    </item>
    <item>
      <title>Comanche sets record</title>
      <link>https://www.sailingscuttlebutt.com/2026/09/02/comanche-record/</link>
      <pubDate>Wed, 02 Sep 2026 10:00:00 +0000</pubDate>
      <description>Comanche smashed the record.</description>
    </item>
  </channel>
</rss>
"""

ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Sail-World</title>
  <entry>
    <title>Sunrise takes IRC crown</title>
    <link rel="alternate" href="https://www.sail-world.com/news/sunrise-irc"/>
    <published>2026-09-02T09:00:00Z</published>
    <content>Sunrise won on corrected time.</content>
  </entry>
</feed>
"""

ARTICLE_HTML = """
<html><head><title>Test</title>
<script>var tracker = 1;</script>
<style>.ad { color: red; }</style>
</head>
<body>
  <h1>Rampage 88 wins offshore classic</h1>
  <p>Rampage 88 (GBR8994R) took line honours after a close battle.</p>
  <script>console.log('ad');</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Feed parsing (RSS 2.0 + Atom)
# ---------------------------------------------------------------------------

def test_parse_rss_feed():
    articles = news.parse_feed(RSS_XML)
    assert len(articles) == 2
    assert articles[0]["url"] == "https://www.sailingscuttlebutt.com/2026/09/01/rampage-wins/"
    assert articles[0]["title"] == "Rampage 88 wins offshore classic"
    assert articles[1]["url"].endswith("comanche-record/")


def test_parse_atom_feed():
    articles = news.parse_feed(ATOM_XML)
    assert len(articles) == 1
    assert articles[0]["url"] == "https://www.sail-world.com/news/sunrise-irc"
    assert articles[0]["title"] == "Sunrise takes IRC crown"


def test_parse_feed_malformed_returns_empty():
    assert news.parse_feed(b"<not xml") == []
    assert news.parse_feed(b"") == []


def test_html_to_text_strips_scripts_and_styles():
    body = news.html_to_text(ARTICLE_HTML)
    assert "Rampage 88 wins offshore classic" in body
    assert "GBR8994R" in body
    assert "console.log" not in body
    assert "var tracker" not in body
    assert ".ad" not in body


def test_domain_of_strips_www():
    assert news._domain_of("https://www.sailingscuttlebutt.com/x") == "sailingscuttlebutt.com"
    assert news._domain_of("https://sail-world.com/x") == "sail-world.com"


# ---------------------------------------------------------------------------
# Zero-Firecrawl guarantee
# ---------------------------------------------------------------------------

def test_news_module_does_not_import_firecrawl_at_top_level():
    """The RSS news path must not pull in the Firecrawl client — that import
    is the mechanism by which news could accidentally spend crawl credits."""
    import inspect
    src = inspect.getsource(news)
    # The only allowed reference is inside the *deprecated* shim's lazy import.
    top_imports = [
        line for line in src.splitlines()
        if line.startswith(("from irc_data.discovery.firecrawl_client",
                            "import firecrawl"))
    ]
    assert top_imports == [], (
        f"news.py must not import Firecrawl at module scope: {top_imports}"
    )


def test_deprecated_firecrawl_path_raises_without_override(monkeypatch):
    monkeypatch.delenv("ALLOW_FIRECRAWL_NEWS", raising=False)
    with pytest.raises(RuntimeError, match="deprecated"):
        import asyncio
        asyncio.run(news.scrape_news_source("https://www.sailingscuttlebutt.com/"))


def test_scrape_news_rss_uses_no_firecrawl(monkeypatch):
    """The RSS ingest must never call Firecrawl. We stub the network fetches
    and Gemini extraction, and assert the Firecrawl client is never touched
    by failing hard if it is imported."""
    import sys

    # Any attempt to import the Firecrawl client inside the RSS path fails.
    monkeypatch.setitem(
        sys.modules, "irc_data.discovery.firecrawl_client", None
    )

    monkeypatch.setattr(news, "_fetch_text", _fake_fetch)
    monkeypatch.setattr(
        news, "extract_boat_mentions",
        lambda url, md: {
            "title": "Rampage 88 wins offshore classic",
            "mentioned_boats": [
                {"boat_name": "Rampage 88", "sail_number": "GBR8994R",
                 "snippet": "Rampage 88 took line honours.", "confidence": 0.95}
            ],
        },
    )
    # Avoid touching the real DB: stub engine + persistence.
    monkeypatch.setattr(news, "get_engine", lambda: object())
    monkeypatch.setattr(news, "_already_processed", lambda engine, url: False)
    monkeypatch.setattr(news, "_persist_article", lambda engine, url, t, b, m: len(m))

    import asyncio
    stats = asyncio.run(
        news.scrape_news_rss(["https://www.sailingscuttlebutt.com/feed"], 5)
    )
    assert stats["articles_seen"] == 2
    assert stats["articles_new"] == 2
    assert stats["articles_processed"] == 2
    assert stats["mentions_matched"] == 2


async def _fake_fetch(client, url):
    if url.endswith("/feed"):
        return RSS_XML.decode("utf-8")
    return ARTICLE_HTML
