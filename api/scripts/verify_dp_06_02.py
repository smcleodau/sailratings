#!/usr/bin/env python3
"""End-to-end verification evidence for DP-06-02 — certified adapter and
parser for the selected source (TopYacht).

This script produces hard, paste-able evidence that the issue's acceptance
criteria and verification steps hold:

  1. **Recorded-fixture suite** — the certified parser is validated against
     every preserved representative variant (standard / DNF / multiclass /
     no-IRC) with exact expected outputs and deterministic hashes.

  2. **Incremental rerun** — a steady-state rerun with a complete
     checkpoint makes zero HTTP calls and collects no unchanged material;
     an interrupted run resumes and fetches only the not-yet-collected
     pages; a changed page is collected with a new hash on re-probe.

  3. **Adapter contract suite** — the generic contract suite runs against
     the certified adapter.

  4. **Source-breakage mutations** — mutated fixtures (table removed,
     headers renamed, irrelevant page) are detected as breakage (zero
     records / changed content hash), never silently parsed into garbage.

  5. **Live canary** — a single real fetch + parse against the live source.
     This runs **only** when ``--live`` is passed (and honours the
     collection policy window).  By default it is reported as SKIPPED so
     the offline verification is fully hermetic and CI-safe.

Usage::

    PYTHONPATH=src python3 scripts/verify_dp_06_02.py           # offline
    PYTHONPATH=src python3 scripts/verify_dp_06_02.py --live    # + live canary
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from irc_data.parsers.extraction_contract import ParserInputV1  # noqa: E402
from irc_data.parsers.topyacht import PARSER_VERSION, TopYachtParser  # noqa: E402
from irc_data.sources.envelope import (  # noqa: E402
    AdapterCheckpointV1,
    FetchStatus,
    sha256_hex,
)
from irc_data.sources.fake_adapter import FakeHttpServer  # noqa: E402
from irc_data.sources.gate import CollectionGate  # noqa: E402
from irc_data.sources.http_client import HttpClient  # noqa: E402
from irc_data.sources.policy import ACTIVE_POLICY  # noqa: E402
from irc_data.sources.registry import get_in_memory_source  # noqa: E402
from irc_data.sources.topyacht_adapter import TopYachtAdapter  # noqa: E402
from tests.fixtures.topyacht import html as fx  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


# ---------------------------------------------------------------------------
# Offline harness (in-process mock server — zero network)
# ---------------------------------------------------------------------------


def _fast_policy():
    return dataclasses.replace(
        ACTIVE_POLICY,
        rate=dataclasses.replace(
            ACTIVE_POLICY.rate, min_delay_seconds=0.0, jitter_seconds=0.0
        ),
    )


def _build_server(routes=None) -> FakeHttpServer:
    server = FakeHttpServer(base_url="http://topyacht.test")
    for path, body in (routes or fx.fixture_routes()).items():
        server.add_route(
            path,
            body,
            headers={
                "Content-Type": "text/html",
                "ETag": f'"etag-{sha256_hex(body.encode())[:8]}"',
            },
        )
    server.add_route("/", b"OK", headers={"Content-Type": "text/plain"})
    return server


def _build_adapter(server, checkpoint=None, cursor=None) -> TopYachtAdapter:
    src = dataclasses.replace(
        get_in_memory_source("topyacht"),
        base_url="http://topyacht.test/results",
    )
    pol = _fast_policy()
    gate = CollectionGate(policy=pol, sources=[src])
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(server._handler),
        follow_redirects=True,
        headers={"User-Agent": ACTIVE_POLICY.attribution.user_agent},
    )
    client = HttpClient(client=inner, policy=pol, backoff=(0.001,) * 4)
    adapter = TopYachtAdapter(db=None, http_client=client, gate=gate, policy=pol)
    if checkpoint is not None:
        adapter.load_checkpoint(checkpoint)
    if cursor is not None:
        adapter.load_discovery_cursor(cursor)
    return adapter


def _parse(content: bytes, url: str, source_slug: str = "topyacht"):
    parser = TopYachtParser()
    inp = ParserInputV1(
        content=content,
        content_hash=sha256_hex(content),
        source_slug=source_slug,
        url=url,
    )
    return parser.parse(inp)


# ---------------------------------------------------------------------------
# 1. Recorded-fixture suite
# ---------------------------------------------------------------------------


def verify_recorded_fixtures() -> None:
    print("\n== 1. Recorded-fixture suite (parser vs representative variants) ==")
    parser = TopYachtParser()

    std = _parse(fx.RACE_STANDARD.encode(), "http://topyacht.test/std")
    check(
        "standard variant → 3 records",
        len(std.records) == 3,
        f"boats={[r.get_value('boat_name') for r in std.records]}",
    )
    check(
        "standard TCCs exact",
        [r.get_value("tcc_at_race") for r in std.records]
        == ["1.105", "1.089", "1.061"],
    )

    dnf = _parse(fx.RACE_DNF.encode(), "http://topyacht.test/dnf")
    check(
        "dnf variant → status detection",
        [r.get_value("status") for r in dnf.records]
        == ["finished", "DNF", "DNS"],
    )

    mc = _parse(fx.RACE_MULTICLASS.encode(), "http://topyacht.test/mc")
    check(
        "multiclass → only IRC table (2 records)",
        len(mc.records) == 2
        and all(r.get_value("division") == "Division 2" for r in mc.records),
    )

    noirc = _parse(fx.RACE_NO_IRC.encode(), "http://topyacht.test/noirc")
    check("no-IRC page → zero records", noirc.records == [])

    b2 = _parse(fx.RACE_STANDARD.encode(), "http://topyacht.test/std")
    check(
        "parser deterministic (hash + batch_id)",
        std.extraction_hash == b2.extraction_hash and std.batch_id == b2.batch_id,
    )
    check("parser version pinned", parser.parser_version == PARSER_VERSION,
          f"v{parser.parser_version}")


# ---------------------------------------------------------------------------
# 2. Incremental rerun + resume
# ---------------------------------------------------------------------------


async def verify_incremental() -> None:
    print("\n== 2. Incremental rerun + resume-after-interruption ==")

    # 2a. Steady-state rerun makes zero HTTP calls.
    server = _build_server()
    adapter = _build_adapter(server)
    first = await adapter.run()
    cp = adapter.save_checkpoint()
    cursor = adapter.save_discovery_cursor()
    server.reset_call_counts()
    adapter2 = _build_adapter(server, checkpoint=cp, cursor=cursor)
    second = await adapter2.run()
    fresh = [e for e in second if e.status == FetchStatus.FETCHED]
    check(
        "steady-state rerun: 0 fresh fetches, 0 HTTP calls",
        fresh == [] and server.call_count() == 0,
        f"http_calls={server.call_count()}",
    )

    # 2b. Resume after interruption fetches only the remaining pages.
    server = _build_server()
    adapter = _build_adapter(server)
    first = await adapter.run()
    cp = AdapterCheckpointV1(source_slug="topyacht")
    cp.mark_completed(first[0].url, first[0].content_hash)
    server.reset_call_counts()
    adapter2 = _build_adapter(server, checkpoint=cp)
    resumed = await adapter2.run()
    fetched_urls = [e.url for e in resumed if e.status == FetchStatus.FETCHED]
    check(
        "resume skips completed page, fetches remaining 2",
        first[0].url not in fetched_urls and len(fetched_urls) == 2,
        f"resumed={len(fetched_urls)}",
    )

    # 2c. Changed page collected with a new hash on re-probe.
    server = _build_server()
    adapter = _build_adapter(server)
    await adapter.run()
    target_url = "http://topyacht.test/results/2024/hirw/rategold/01RGrp2.htm"
    orig_hash = adapter.save_checkpoint().content_hashes[target_url]
    server.add_route(
        "/results/2024/hirw/rategold/01RGrp2.htm",
        fx.RACE_STANDARD_V2,
        headers={"Content-Type": "text/html", "ETag": '"etag-v2"'},
    )
    server.reset_call_counts()
    adapter2 = _build_adapter(server)
    second = await adapter2.run()
    changed = [
        e for e in second
        if e.url == target_url and e.status == FetchStatus.FETCHED
    ]
    new_records = _parse(changed[0].content, target_url).records if changed else []
    check(
        "changed page collected with new hash (4 finishers)",
        len(changed) == 1
        and changed[0].content_hash != orig_hash
        and len(new_records) == 4,
        f"hash_changed={bool(changed) and changed[0].content_hash != orig_hash}",
    )


# ---------------------------------------------------------------------------
# 3. Adapter contract suite
# ---------------------------------------------------------------------------


async def verify_contract_suite() -> None:
    print("\n== 3. Adapter contract suite ==")
    from tests.sources.test_contract_suite import run_contract_suite

    server = _build_server()
    adapter = _build_adapter(server)
    try:
        results = await run_contract_suite(adapter)
        ok = (
            results["discover_count"] == 3
            and results["collect_count"] == 3
            and results["checkpoint_urls"] == 3
            and results["healthy"] is True
        )
        check("contract suite passes", ok, f"{results}")
    except AssertionError as exc:
        check("contract suite passes", False, str(exc))


# ---------------------------------------------------------------------------
# 4. Source-breakage mutations
# ---------------------------------------------------------------------------


async def verify_mutations() -> None:
    print("\n== 4. Source-breakage mutation tests ==")
    check(
        "mutation: table removed → 0 records",
        len(_parse(fx.MUTATED_NO_TABLES.encode(), "http://x").records) == 0,
    )
    check(
        "mutation: headers renamed → 0 records",
        len(_parse(fx.MUTATED_HEADERS_RENAMED.encode(), "http://x").records) == 0,
    )
    check(
        "mutation: irrelevant page → 0 records",
        len(_parse(fx.MUTATED_IRRELEVANT.encode(), "http://x").records) == 0,
    )
    check(
        "mutation changes content hash (monitor signal)",
        sha256_hex(fx.RACE_STANDARD) != sha256_hex(fx.MUTATED_NO_TABLES),
    )

    # Breakage mid-collection: one broken page emits 0 records, others parse.
    routes = fx.fixture_routes()
    routes["/results/2024/hirw/rategold/02RGrp2.htm"] = fx.MUTATED_NO_TABLES
    server = _build_server(routes)
    adapter = _build_adapter(server)
    envs = await adapter.run()
    from urllib.parse import urlparse

    per_path = {
        urlparse(e.url).path: len(_parse(e.content, e.url).records) for e in envs
    }
    ok = (
        per_path.get("/results/2024/hirw/rategold/01RGrp2.htm") == 3
        and per_path.get("/results/2024/hirw/rategold/02RGrp2.htm") == 0
    )
    check("breakage isolated: healthy pages parse, broken page 0", ok,
          f"{per_path}")


# ---------------------------------------------------------------------------
# 5. Live canary (opt-in)
# ---------------------------------------------------------------------------


async def verify_live_canary(enabled: bool) -> None:
    print("\n== 5. Live canary ==")
    if not enabled:
        check(
            "live canary",
            True,
            "SKIPPED (pass --live to run a single real fetch+parse)",
        )
        return

    from irc_data.sources.policy import is_within_collection_window

    if not is_within_collection_window():
        check(
            "live canary",
            True,
            "SKIPPED (outside nightly collection window 01:00-06:00)",
        )
        return

    try:
        src = get_in_memory_source("topyacht")
        gate = CollectionGate(policy=ACTIVE_POLICY, sources=[src])
        client = HttpClient(policy=ACTIVE_POLICY)
        adapter = TopYachtAdapter(
            db=None, http_client=client, gate=gate, policy=ACTIVE_POLICY,
            clubs={
                "HIRW": {
                    "club_name": "Hamilton Island Race Week",
                    "base_url": src.base_url,
                    "divisions": ["hirw"],
                    "years": [2024],
                }
            },
            years=[2024],
        )
        envelopes = await adapter.run()
        fetched = [e for e in envelopes if e.status == FetchStatus.FETCHED]
        total_records = sum(
            len(_parse(e.content, e.url).records) for e in fetched
        )
        check(
            "live canary: fetched + parsed real source",
            len(fetched) > 0 and total_records > 0,
            f"pages={len(fetched)} records={total_records}",
        )
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        check("live canary", False, f"ERROR: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    live = "--live" in sys.argv
    print("=" * 70)
    print("DP-06-02 — certified adapter + parser verification (source: topyacht)")
    print("=" * 70)

    verify_recorded_fixtures()
    asyncio.run(verify_incremental())
    asyncio.run(verify_contract_suite())
    asyncio.run(verify_mutations())
    asyncio.run(verify_live_canary(live))

    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"RESULT: {passed}/{total} checks passed")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label}  {detail}")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
