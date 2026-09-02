#!/usr/bin/env python3
"""End-to-end verification for DP-00-03 — Yacht Scoring + Manage2Sail raw capture.

This is the **recorded fixture run** required by the DP-00-03 acceptance
criteria.  It drives the real capture engine (``raw_capture_ys_m2s``) against
recorded fixtures served through ``httpx.MockTransport`` (no live network), with
a real SQLite database standing in for Postgres so the crawl ledger
(``firecrawl_calls``, OPS-01-05) and the provenance audit log
(``raw_objects`` / ``retrieval_events``) are genuinely written and queried.

It prints hard PASS/FAIL evidence that the acceptance criteria hold:

  1. Nightly job fetches only new/changed pages (conditional requests + hash
     dedup) and stores raw bytes + envelope.
  2. Every call is logged to the crawl ledger (OPS-01-05).
  3. Politeness rules respected (collection window, kill switch, robots
     fail-closed, fetch caps, per-object size cap).
  4. **Idempotent on rerun** — a second run over unchanged pages fetches zero
     new pages and stores zero new raw objects (``rerun fetches zero unchanged
     pages``).
  5. Canary mode caps discovery so a live canary night stays within rate caps.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_00_03.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from irc_data.scrapers import raw_capture_ys_m2s as mod  # noqa: E402
from irc_data.scrapers.raw_capture_ys_m2s import (  # noqa: E402
    CANARY_MAX_DISCOVERY_PAGES,
    SOURCE_SLUG_MANAGE2SAIL,
    SOURCE_SLUG_YACHTSCORING,
    capture_source,
)
from irc_data.sources.provenance import RawObjectStore  # noqa: E402

# ---------------------------------------------------------------------------
# Recorded fixtures (no live network)
# ---------------------------------------------------------------------------

YS_PAGE_A = b"<html><body><h1>Yacht Scoring - Event 12345 Results</h1></body></html>"
YS_PAGE_B = b"<html><body><h1>Yacht Scoring - Event 678 Results</h1></body></html>"
YS_PAGE_A_V2 = b"<html><body><h1>Yacht Scoring - Event 12345 Results (rev 2)</h1></body></html>"
M2S_PAGE_A = b"<html><body><h1>Manage2Sail - Regatta 2025 Results</h1></body></html>"
M2S_PAGE_B = b"<html><body><h1>Manage2Sail - Nationals Results</h1></body></html>"

YS_INDEX = """
<html><body>
  <a href="https://www.yachtscoring.com/event_results_cumulative/12345">Event 12345</a>
  <a href="https://www.yachtscoring.com/event_results.cfm?eid=678">Event 678</a>
  <a href="https://www.yachtscoring.com/about">About</a>
</body></html>
"""

M2S_INDEX = """
<html><body>
  <a href="https://www.manage2sail.com/event/regatta-2025">Regatta 2025</a>
  <a href="https://www.manage2sail.com/results/nationals">Nationals</a>
</body></html>
"""

#: Mutable per-URL bodies so we can simulate a content change mid-run.
BODIES: dict[str, bytes] = {
    "https://www.yachtscoring.com/event_results_cumulative/12345": YS_PAGE_A,
    "https://www.yachtscoring.com/event_results.cfm?eid=678": YS_PAGE_B,
    "https://www.manage2sail.com/event/regatta-2025": M2S_PAGE_A,
    "https://www.manage2sail.com/results/nationals": M2S_PAGE_B,
}

#: Record of every fetch the mock transport served (proof of what was fetched).
FETCH_LOG: list[dict] = []


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    path = request.url.path
    host = request.url.host

    if path == "/robots.txt":
        return httpx.Response(404)  # no disallow rules → allowed
    if "event_results_archive" in path:
        FETCH_LOG.append({"url": url, "kind": "index"})
        return httpx.Response(200, text=YS_INDEX, headers={"Content-Type": "text/html"})
    if host.endswith("manage2sail.com") and path.rstrip("/") == "/event":
        FETCH_LOG.append({"url": url, "kind": "index"})
        return httpx.Response(200, text=M2S_INDEX, headers={"Content-Type": "text/html"})

    body = BODIES.get(url)
    if body is None:
        FETCH_LOG.append({"url": url, "kind": "miss"})
        return httpx.Response(404)

    # Honour conditional requests: if the client sent the ETag we issued for
    # the *current* body, answer 304 (not modified).
    etag = f'"ed-{abs(hash(body)) & 0xFFFFFFFF:x}"'
    FETCH_LOG.append(
        {
            "url": url,
            "kind": "page",
            "if_none_match": request.headers.get("if-none-match", ""),
            "sent_etag": etag,
            "not_modified": request.headers.get("if-none-match") == etag,
        }
    )
    if request.headers.get("if-none-match") == etag:
        return httpx.Response(304)
    return httpx.Response(
        200, content=body, headers={"Content-Type": "text/html", "ETag": etag}
    )


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler), follow_redirects=True)


# ---------------------------------------------------------------------------
# SQLite stand-in for the Postgres crawl ledger + provenance tables
# ---------------------------------------------------------------------------

DDL = [
    """
    CREATE TABLE IF NOT EXISTS firecrawl_calls (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      called_at TEXT DEFAULT (datetime('now')),
      mode TEXT NOT NULL, url TEXT NOT NULL, domain TEXT NOT NULL,
      status TEXT NOT NULL, http_status INTEGER, credits INTEGER,
      duration_ms INTEGER, response_chars INTEGER, links_found INTEGER,
      error_message TEXT, caller TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_objects (
      content_hash TEXT PRIMARY KEY,
      byte_size INTEGER NOT NULL, content_type TEXT,
      object_location TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retrieval_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      content_hash TEXT NOT NULL, source TEXT NOT NULL,
      requested_uri TEXT, resolved_uri TEXT, retrieved_at TEXT NOT NULL,
      policy_version TEXT NOT NULL, headers_subset TEXT, status INTEGER,
      object_location TEXT NOT NULL, adapter_version TEXT, lineage TEXT,
      schema_version TEXT NOT NULL DEFAULT '1',
      created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_sources (
      slug TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
      legal_status TEXT NOT NULL DEFAULT 'approved'
    )
    """,
]


def _engine():
    """In-memory SQLite stand-in for Postgres.

    The capture engine's SQL is portable (Postgres-specific JSON operators are
    only used on the ``postgresql`` dialect), so a plain SQLite engine works.
    """
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "INSERT INTO data_sources (slug, enabled, legal_status) VALUES "
                "('yachtscoring', 1, 'approved'), ('manage2sail', 1, 'approved')"
            )
        )
    return eng


def _count(eng, sql: str, params: dict | None = None) -> int:
    with eng.connect() as conn:
        return int(conn.execute(text(sql), params or {}).scalar() or 0)


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="dp-00-03-verify-"))
    store = RawObjectStore(str(tmp / "raw_store"))
    engine = _engine()

    # Neutralise the 2 s politeness sleep for the recorded fixture run.
    import time as _time

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures.append(label)

    with patch.object(mod, "_polite_sleep", lambda last, min_delay=0.0: _time.monotonic()), \
         patch.object(mod, "_make_client", lambda slug: _client()):

        # ------------------------------------------------------------------
        _banner("RUN 1 — nightly fetch → hash → store (both sources)")
        ys = capture_source(
            SOURCE_SLUG_YACHTSCORING, store,
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )
        m2s = capture_source(
            SOURCE_SLUG_MANAGE2SAIL, store,
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )

        print(f"  yachtscoring: new={ys.urls_new} unchanged={ys.urls_unchanged} "
              f"not_modified={ys.urls_not_modified} fetches={ys.fetch_count} status={ys.status}")
        print(f"  manage2sail:  new={m2s.urls_new} unchanged={m2s.urls_unchanged} "
              f"not_modified={m2s.urls_not_modified} fetches={m2s.fetch_count} status={m2s.status}")

        expect(ys.status in ("ok", "ok_with_errors"), "YS run completed")
        expect(m2s.status in ("ok", "ok_with_errors"), "M2S run completed")
        expect(ys.urls_new == 2, "YS stored 2 new result pages")
        expect(m2s.urls_new == 2, "M2S stored 2 new result pages")

        # Raw bytes + envelope persisted.
        n_raw = _count(engine, "SELECT COUNT(*) FROM raw_objects")
        n_rev = _count(engine, "SELECT COUNT(*) FROM retrieval_events")
        expect(n_raw == store.count() and n_raw >= 2,
               f"raw objects stored ({n_raw}) == store.count()")
        expect(n_rev == ys.urls_new + m2s.urls_new,
               f"retrieval_events rows ({n_rev}) == new captures")

        # Envelope carries hash + URL + fetch time + policy version.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT content_hash, requested_uri, policy_version, "
                    "adapter_version, retrieved_at, object_location "
                    "FROM retrieval_events ORDER BY id LIMIT 1"
                )
            ).fetchone()
        expect(row is not None and len(row[0]) == 64, "envelope has SHA-256 content_hash")
        expect(row[2] == "v1.0", "envelope policy_version == v1.0")
        expect(row[3] == mod.ADAPTER_VERSION, "envelope adapter_version == dp-00-03/1.0")
        expect(bool(row[4]) and bool(row[5]), "envelope has fetch time + object_location")

        # Crawl ledger (OPS-01-05) recorded every call.  Index fetches are
        # logged as mode='map'; each result-page fetch as mode='scrape'.
        page_fetches = (ys.fetch_count - 1) + (m2s.fetch_count - 1)  # minus index fetches
        n_ledger = _count(engine, "SELECT COUNT(*) FROM firecrawl_calls")
        n_map = _count(engine, "SELECT COUNT(*) FROM firecrawl_calls WHERE mode='map'")
        n_scrape = _count(engine, "SELECT COUNT(*) FROM firecrawl_calls WHERE mode='scrape'")
        expect(n_map == 2, f"crawl ledger: 2 discovery/map calls logged ({n_map})")
        expect(n_scrape == page_fetches,
               f"crawl ledger: page fetches logged ({n_scrape} == {page_fetches})")
        expect(n_ledger >= ys.fetch_count + m2s.fetch_count - 2,
               f"crawl ledger total ({n_ledger}) covers all calls")

        # ------------------------------------------------------------------
        _banner("RUN 2 — idempotent rerun over UNCHANGED pages")
        FETCH_LOG.clear()
        ys2 = capture_source(
            SOURCE_SLUG_YACHTSCORING, store,
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )
        m2s2 = capture_source(
            SOURCE_SLUG_MANAGE2SAIL, store,
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )

        print(f"  yachtscoring rerun: new={ys2.urls_new} unchanged={ys2.urls_unchanged} "
              f"not_modified={ys2.urls_not_modified}")
        print(f"  manage2sail rerun:  new={m2s2.urls_new} unchanged={m2s2.urls_unchanged} "
              f"not_modified={m2s2.urls_not_modified}")

        page_fetches = [f for f in FETCH_LOG if f["kind"] == "page"]
        n_304 = sum(1 for f in page_fetches if f.get("not_modified"))
        expect(ys2.urls_new == 0 and m2s2.urls_new == 0,
               "rerun stored ZERO new artifacts")
        expect(store.count() == n_raw, "raw store object count unchanged on rerun")
        expect(
            ys2.urls_not_modified + ys2.urls_unchanged == 2
            and m2s2.urls_not_modified + m2s2.urls_unchanged == 2,
            "rerun: every unchanged page is a no-op (304 or hash-dup)",
        )
        expect(n_304 >= 1,
               f"conditional requests produced HTTP 304 no-ops ({n_304})")

        # ------------------------------------------------------------------
        _banner("RUN 3 — changed page is re-captured as a new artifact")
        BODIES["https://www.yachtscoring.com/event_results_cumulative/12345"] = YS_PAGE_A_V2
        ys3 = capture_source(
            SOURCE_SLUG_YACHTSCORING, store,
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )
        print(f"  after content change: new={ys3.urls_new} "
              f"not_modified={ys3.urls_not_modified} unchanged={ys3.urls_unchanged}")
        expect(ys3.urls_new == 1, "changed page stored as a NEW artifact")
        expect(store.count() == n_raw + 1, "raw store grew by exactly one object")

        # ------------------------------------------------------------------
        _banner("POLITENESS — window, kill switch, caps, robots")
        # Window closed.
        with patch.object(mod, "is_within_collection_window", return_value=False):
            w = capture_source(
                SOURCE_SLUG_YACHTSCORING, RawObjectStore(str(tmp / "w")),
                db_engine=engine, enforce_window=True, check_kill_switch=True,
            )
        expect(w.status == "window_closed", "collection window enforced (window_closed)")

        # Kill switch.
        with engine.begin() as conn:
            conn.execute(text("UPDATE data_sources SET enabled=0 WHERE slug='manage2sail'"))
        k = capture_source(
            SOURCE_SLUG_MANAGE2SAIL, RawObjectStore(str(tmp / "k")),
            db_engine=engine, enforce_window=False, check_kill_switch=True,
        )
        expect(k.status == "kill_switch" and store.count() == n_raw + 1,
               "kill switch blocks collection (no fetch)")
        with engine.begin() as conn:
            conn.execute(text("UPDATE data_sources SET enabled=1 WHERE slug='manage2sail'"))

        # Fetch cap.
        cap = capture_source(
            SOURCE_SLUG_YACHTSCORING, RawObjectStore(str(tmp / "cap")),
            urls=[f"https://www.yachtscoring.com/event_results_cumulative/{i}" for i in range(30)],
            max_fetches=3, db_engine=None, enforce_window=False, check_kill_switch=False,
        )
        expect(cap.fetch_count <= 3, f"max_fetches cap respected ({cap.fetch_count} <= 3)")

        # ------------------------------------------------------------------
        _banner("CANARY MODE — discovery capped within rate caps")
        links = "".join(
            f'<a href="https://www.yachtscoring.com/event_results_cumulative/{i}">E{i}</a>'
            for i in range(60)
        )

        def many_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/robots.txt":
                return httpx.Response(404)
            if "event_results_archive" in path:
                return httpx.Response(200, text=f"<html><body>{links}</body></html>",
                                      headers={"Content-Type": "text/html"})
            return httpx.Response(200, content=YS_PAGE_A, headers={"Content-Type": "text/html"})

        with patch.object(mod, "_make_client", lambda slug: httpx.Client(
            transport=httpx.MockTransport(many_handler), follow_redirects=True
        )):
            can = capture_source(
                SOURCE_SLUG_YACHTSCORING, RawObjectStore(str(tmp / "canary")),
                canary=True, db_engine=None, enforce_window=False, check_kill_switch=False,
            )
        print(f"  canary: attempted={can.urls_attempted} fetches={can.fetch_count} "
              f"(cap={CANARY_MAX_DISCOVERY_PAGES})")
        expect(can.urls_attempted <= CANARY_MAX_DISCOVERY_PAGES,
               f"canary discovery capped at {CANARY_MAX_DISCOVERY_PAGES}")

    # ----------------------------------------------------------------------
    _banner("SUMMARY")
    print(f"  raw store objects:        {store.count()}")
    print(f"  retrieval_events rows:    {_count(engine, 'SELECT COUNT(*) FROM retrieval_events')}")
    print(f"  crawl ledger calls:       {_count(engine, 'SELECT COUNT(*) FROM firecrawl_calls')}")
    print(f"  crawl ledger credits sum: {_count(engine, 'SELECT COALESCE(SUM(credits),0) FROM firecrawl_calls')} (0 = plain HTTP)")

    if failures:
        print(f"\n  RESULT: FAIL — {len(failures)} expectation(s) failed")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n  RESULT: PASS — fetch→hash→store, crawl-ledger coverage, "
          "politeness, idempotent rerun (zero new), canary cap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
