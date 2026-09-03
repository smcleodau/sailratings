"""OPS-02-02: per-club/per-run error text must never be empty.

Context: on 2026-09-02, 55 of 76 SailSys runs ended
``status='completed_with_errors'`` with ``error_message = NULL``/'' while
reporting found>0 / new=0. The status claimed something failed, but the row
carried no evidence of *what*. Two root defects:

1. ``result_import.import_scraper_results`` marked the per-club run
   ``completed_with_errors`` but never passed ``error_message`` to
   ``log_ingestion_end``.
2. Exception text was rendered with plain ``str(e)`` / ``f"{e}"``, which is
   empty for several exception types (e.g. ``RuntimeError()``,
   ``asyncio.CancelledError``) — producing messages like ``'SSORC: '``.

Fixes under test:
* ``scrape_supervision.require_error_message`` — the contract: error statuses
  always persist a non-empty message.
* ``scrapers.sailsys._exc_text`` — never-blank exception rendering.
* ``scrapers.sailsys.scrape_club_irc_results_detailed`` — per-series/race
  exceptions land in ``ClubScrapeResult.errors`` (incl. a 522 from
  api.sailsys.com.au).
* ``scrapers.sailsys.scrape_club_irc_results`` — legacy list-returning
  signature preserved, with an ``on_errors`` callback for the run supervisor.
"""

import asyncio

import httpx
import pytest

from irc_data.parsers.schemas import RaceResult
from irc_data.scrape_supervision import ERROR_STATUSES, require_error_message
from irc_data.scrapers import result_import, sailsys
from irc_data.scrapers.sailsys import (
    ClubScrapeResult,
    _exc_text,
    scrape_club_irc_results,
    scrape_club_irc_results_detailed,
)

# ---------------------------------------------------------------------------
# SailSys API fixtures
# ---------------------------------------------------------------------------

_CLUB_17_GROUPING = {
    "data": [
        {"id": 1, "name": "2026", "series": [{"id": 999, "name": "ASC Twilight", "clubId": 17}]}
    ]
}

_SERIES_999_RACES = {
    "data": {
        "id": 999,
        "name": "ASC Twilight",
        "races": [
            {"id": 555, "name": "Race 1", "status": 4, "dateTime": "2026-08-29T15:00:00.000"},
        ],
    }
}

_RACE_555_RESULTS = {
    "data": {
        "id": 555,
        "name": "Race 1",
        "dateTime": "2026-08-29T15:00:00.000",
        "competitors": [
            {
                "parent": {"name": "Division 1"},
                "items": [
                    {
                        "boat": {"name": "TEST BOAT", "sailNumber": "1", "club": "ASC"},
                        "skipper": {"profile": {"fullName": "A Sailor"}},
                        "handicap": {"currentHandicaps": [
                            {"definition": {"id": 33}, "value": "1.001"},
                        ]},
                        "calculations": [
                            {"handicapDefinitionId": 33, "correctedTime": "01:02:03",
                             "placings": [{"place": 1, "placingText": "1"}]},
                        ],
                        "elapsedTime": "01:00:00",
                        "finishTimeLocal": "2026-08-29T16:00:00",
                    }
                ],
            }
        ],
    }
}


def _sailsys_transport(handler_overrides: dict | None = None) -> httpx.MockTransport:
    """Mock api.sailsys.com.au. handler_overrides maps URL path -> callable."""
    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if handler_overrides and path in handler_overrides:
            return handler_overrides[path](request)
        if path == "/api/v1/series/club/17/grouping/list":
            return httpx.Response(200, json=_CLUB_17_GROUPING)
        if path == "/api/v1/series/999/display/races":
            return httpx.Response(200, json=_SERIES_999_RACES)
        if path == "/api/v1/races/555/resultsentrants/display":
            return httpx.Response(200, json=_RACE_555_RESULTS)
        return httpx.Response(404, json={"errorMessage": f"unmocked {path}"})
    return httpx.MockTransport(_handler)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    async def _instant(self):
        return None
    monkeypatch.setattr(sailsys.RateLimiter, "wait", _instant)


# ---------------------------------------------------------------------------
# require_error_message — the supervision contract
# ---------------------------------------------------------------------------

class TestRequireErrorMessage:
    @pytest.mark.parametrize("status", sorted(ERROR_STATUSES))
    def test_error_status_with_none_message_gets_fallback(self, status):
        msg = require_error_message(status, None, context="club SASC")
        assert msg and msg.strip()
        assert "club SASC" in msg

    @pytest.mark.parametrize("status", sorted(ERROR_STATUSES))
    def test_error_status_with_blank_message_gets_fallback(self, status):
        assert require_error_message(status, "", context="x").strip()
        assert require_error_message(status, "   ", context="x").strip()

    @pytest.mark.parametrize("status", sorted(ERROR_STATUSES))
    def test_error_status_preserves_real_message(self, status):
        real = "SASC: 3 errors [series 9: HTTPStatusError: 522]"
        assert require_error_message(status, real) == real

    def test_completed_passes_message_through_untouched(self):
        assert require_error_message("completed", None) is None
        assert require_error_message("completed", "info note") == "info note"

    def test_fallback_includes_status(self):
        assert require_error_message("completed_with_errors", None).startswith(
            "completed_with_errors"
        )


# ---------------------------------------------------------------------------
# _exc_text — never-blank exception rendering
# ---------------------------------------------------------------------------

class TestExcText:
    def test_http_status_error_includes_code_and_url(self):
        req = httpx.Request("GET", "https://api.sailsys.com.au/api/v1/series/club/17/grouping/list")
        resp = httpx.Response(522, request=req)
        e = httpx.HTTPStatusError("Server error '522 <none>'", request=req, response=resp)
        text = _exc_text(e)
        assert "HTTPStatusError" in text
        assert "522" in text

    def test_empty_str_exception_still_non_empty(self):
        # The exact failure mode behind 'SSORC: ' / 'RPEYC: ' log rows.
        assert _exc_text(RuntimeError()) == "RuntimeError"
        assert _exc_text(Exception()) == "Exception"

    def test_cancelled_error_non_empty(self):
        assert _exc_text(asyncio.CancelledError()) == "CancelledError"

    def test_message_preserved(self):
        assert _exc_text(ValueError("bad thing")) == "ValueError: bad thing"


# ---------------------------------------------------------------------------
# ClubScrapeResult
# ---------------------------------------------------------------------------

class TestClubScrapeResult:
    def test_ok_when_no_errors(self):
        r = ClubScrapeResult(club_id=17, club_name="ASC")
        assert r.ok
        assert r.error_text() == ""

    def test_error_text_names_club_and_detail(self):
        r = ClubScrapeResult(club_id=17, club_name="ASC",
                             errors=["series 9 (Twilight): HTTPStatusError: 522"])
        text = r.error_text()
        assert text.startswith("ASC: 1 errors [")
        assert "522" in text

    def test_error_text_truncates_long_lists(self):
        r = ClubScrapeResult(club_id=17, club_name="ASC",
                             errors=[f"err {i}" for i in range(25)])
        text = r.error_text()
        assert "25 errors" in text and "(+15 more)" in text


# ---------------------------------------------------------------------------
# scrape_club_irc_results_detailed — 522 from api.sailsys.com.au
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestScrapeClubErrorCapture:
    async def test_522_on_series_listing_yields_non_empty_error_text(self, monkeypatch):
        """THE acceptance test: a 522 from api.sailsys.com.au surfaces as a
        non-empty per-club error instead of being swallowed to stdout."""
        def _grouping_522(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                522,
                json={"data": None, "errorMessage": "Origin Connection Time-out",
                      "result": "error", "httpCode": 522},
                request=request,
            )

        client = httpx.AsyncClient(
            transport=_sailsys_transport({
                "/api/v1/series/club/17/grouping/list": _grouping_522,
            }),
            timeout=5,
        )
        monkeypatch.setattr(
            sailsys.httpx, "AsyncClient",
            lambda *a, **k: client,
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await scrape_club_irc_results_detailed(club_id=17)

        # The exception itself must render non-empty (this is what the CLI's
        # per-club handler now persists via _exc_text).
        detail = _exc_text(exc_info.value)
        assert detail.strip()
        assert "522" in detail
        assert "HTTPStatusError" in detail

    async def test_522_on_race_listing_captured_in_club_errors(self, monkeypatch):
        """Per-series failure mid-club: run continues, error text collected."""
        def _races_522(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                522,
                json={"data": None, "errorMessage": "Origin Connection Time-out",
                      "result": "error", "httpCode": 522},
                request=request,
            )

        client = httpx.AsyncClient(
            transport=_sailsys_transport({
                "/api/v1/series/999/display/races": _races_522,
            }),
            timeout=5,
        )
        monkeypatch.setattr(sailsys.httpx, "AsyncClient", lambda *a, **k: client)

        outcome = await scrape_club_irc_results_detailed(club_id=17)

        assert not outcome.ok
        assert len(outcome.errors) == 1
        text = outcome.error_text()
        assert text.strip()
        assert "ASC" in text
        assert "series 999" in text
        assert "522" in text
        assert "HTTPStatusError" in text

    async def test_race_level_exception_captured_with_race_context(self, monkeypatch):
        """A race-level exception names series AND race ids."""
        async def _boom(*args, **kwargs):
            raise RuntimeError()  # empty str() — the historical blank-message bug

        monkeypatch.setattr(sailsys, "scrape_race_results", _boom)
        client = httpx.AsyncClient(transport=_sailsys_transport(), timeout=5)
        monkeypatch.setattr(sailsys.httpx, "AsyncClient", lambda *a, **k: client)

        outcome = await scrape_club_irc_results_detailed(club_id=17)

        assert len(outcome.errors) == 1
        msg = outcome.errors[0]
        assert "series 999" in msg and "race 555" in msg
        assert msg.endswith("RuntimeError")  # _exc_text, never blank
        assert outcome.error_text().strip()

    async def test_happy_path_no_errors_and_results_flow(self, monkeypatch):
        client = httpx.AsyncClient(transport=_sailsys_transport(), timeout=5)
        monkeypatch.setattr(sailsys.httpx, "AsyncClient", lambda *a, **k: client)

        outcome = await scrape_club_irc_results_detailed(club_id=17)

        assert outcome.ok
        assert outcome.errors == []
        assert outcome.error_text() == ""
        assert len(outcome.results) == 1
        assert outcome.results[0].raw_data["boat_name"] == "TEST BOAT"


# ---------------------------------------------------------------------------
# scrape_club_irc_results — legacy signature + on_errors callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestLegacyWrapper:
    async def test_returns_list_and_reports_errors_via_callback(self, monkeypatch):
        captured: list[str] = []
        outcome = ClubScrapeResult(
            club_id=17, club_name="ASC",
            results=[], errors=["series 9 (Twilight): HTTPStatusError: 522"],
        )

        async def _fake_detailed(*args, **kwargs):
            return outcome

        monkeypatch.setattr(sailsys, "scrape_club_irc_results_detailed", _fake_detailed)

        results = await scrape_club_irc_results(club_id=17, on_errors=captured.append)

        assert isinstance(results, list)
        assert captured == ["ASC: 1 errors [series 9 (Twilight): HTTPStatusError: 522]"]
        assert captured[0].strip()

    async def test_callback_not_called_when_clean(self, monkeypatch):
        captured: list[str] = []
        outcome = ClubScrapeResult(club_id=17, club_name="ASC", results=["r1"], errors=[])

        async def _fake_detailed(*args, **kwargs):
            return outcome

        monkeypatch.setattr(sailsys, "scrape_club_irc_results_detailed", _fake_detailed)

        results = await scrape_club_irc_results(club_id=17, on_errors=captured.append)
        assert results == ["r1"]
        assert captured == []

    async def test_on_errors_optional(self, monkeypatch):
        outcome = ClubScrapeResult(club_id=17, club_name="ASC", errors=["x"])

        async def _fake_detailed(*args, **kwargs):
            return outcome

        monkeypatch.setattr(sailsys, "scrape_club_irc_results_detailed", _fake_detailed)
        # No callback: must not raise even though errors exist.
        assert await scrape_club_irc_results(club_id=17) == []


# ---------------------------------------------------------------------------
# import_scraper_results — completed_with_errors rows must carry the error text
# ---------------------------------------------------------------------------

class _FakeResult:
    """Minimal stand-in for the object SQLAlchemy's conn.execute returns."""
    def __init__(self, value=None):
        self._v = value
    def scalar_one(self):
        return self._v


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeResult(1)


class _FakeBegin:
    def __enter__(self):
        return _FakeConn()
    def __exit__(self, *a):
        return False


class _FakeEngine:
    def begin(self):
        return _FakeBegin()
    def connect(self):
        return _FakeBegin()


def _make_result(name="X", event="Series - Race 1"):
    return RaceResult(
        event_name=event,
        event_date=None,
        source_url="https://example.com/x",
        tcc_at_race=None,
        place=1,
        status=None,
        raw_data={"boat_name": name},
    )


class TestImportScraperResultsErrorCapture:
    """The original OPS-02-02 bug: per-club import failures were swallowed and
    the ingestion_log row was written completed_with_errors + NULL message."""

    def _patch(self, monkeypatch, captured, upsert_behaviour):
        monkeypatch.setattr(result_import, "log_ingestion_start", lambda *a, **k: 1)
        monkeypatch.setattr(
            result_import, "log_ingestion_end",
            lambda engine, log_id, **kw: captured.append(kw),
        )
        # Never hit the DB for boat matching.
        monkeypatch.setattr(result_import, "find_boat_by_sail_number", lambda *a, **k: None)
        monkeypatch.setattr(result_import, "_find_boat_by_name", lambda *a, **k: None)
        monkeypatch.setattr(result_import, "upsert_race_result", upsert_behaviour)

    def test_failing_import_writes_non_empty_error_message(self, monkeypatch):
        captured: list[dict] = []

        def _boom(*a, **k):
            raise KeyError("organizing_club")  # the real exception seen in prod

        self._patch(monkeypatch, captured, _boom)
        stats = result_import.import_scraper_results(
            _FakeEngine(), [_make_result("Capitano")], source="sailsys",
            organizing_club="HHYC",
        )

        assert stats["errors"] == 1
        assert captured, "log_ingestion_end was not called"
        kw = captured[0]
        assert kw["status"] == "completed_with_errors"
        assert kw["error_message"] and kw["error_message"].strip()
        assert "KeyError" in kw["error_message"]
        assert "organizing_club" in kw["error_message"]
        assert "Capitano" in kw["error_message"]

    def test_clean_import_leaves_error_message_none(self, monkeypatch):
        captured: list[dict] = []

        def _ok(*a, **k):
            return 1

        self._patch(monkeypatch, captured, _ok)
        stats = result_import.import_scraper_results(
            _FakeEngine(), [_make_result()], source="sailsys", organizing_club="HHYC",
        )

        assert stats["errors"] == 0 and stats["imported"] == 1
        assert captured[0]["status"] == "completed"
        assert captured[0]["error_message"] is None
