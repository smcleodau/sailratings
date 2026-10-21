from datetime import date
from types import SimpleNamespace

from irc_data.scrapers.sailsys import select_races_to_scrape
from irc_data.temporal.legacy_adapters import drop_reused_race_copies


def _race(rid, d, status="Completed"):
    return {"race_id": rid, "date": f"{d}T00:00:00.000", "status": status, "name": f"Race {rid}"}


def test_skips_future_unscored_and_before_since():
    races = [_race(1, "2026-10-04"), _race(2, "2026-10-18"), _race(3, "2026-11-22"),
             _race(4, "2026-10-19", status="Scheduled")]
    picked = select_races_to_scrape(races, since=date(2026, 10, 7), today=date(2026, 10, 21),
                                    completed_statuses={"Completed"})
    assert [r["race_id"] for r in picked] == [2]


def test_no_since_still_drops_future_races():
    races = [_race(1, "2025-11-23"), _race(2, "2026-11-22")]
    picked = select_races_to_scrape(races, since=None, today=date(2026, 10, 21),
                                    completed_statuses={"Completed"})
    assert [r["race_id"] for r in picked] == [1]


def _res(rid, d, boat="Bandit"):
    return SimpleNamespace(event_date=date.fromisoformat(d), raw_data={"race_id": rid, "boat_name": boat})


def test_drops_results_whose_race_id_is_stored_under_another_date():
    existing = {"37077": {date(2025, 10, 19)}, "46237": {date(2026, 10, 3)}}
    results = [_res(37077, "2026-10-18"), _res(46237, "2026-10-03"), _res(50000, "2026-10-18")]
    kept, dropped = drop_reused_race_copies(results, existing)
    assert [r.raw_data["race_id"] for r in kept] == [46237, 50000]
    assert dropped == 1


def test_hidden_series_returns_no_races_instead_of_raising():
    import asyncio
    import httpx
    from irc_data.scrapers import sailsys

    def handler(req):
        return httpx.Response(400, json={"data": None, "result": "error", "httpCode": 400,
                                         "errorMessage": "This series is not displaying entrants or results."})

    sailsys.rate_limiter.min_delay = sailsys.rate_limiter.jitter = 0

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sailsys.get_series_races(c, 3435)

    assert asyncio.run(run()) == []
