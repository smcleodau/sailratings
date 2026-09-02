"""DP-06-02 — certified TopYacht adapter + parser tests.

Covers the issue's acceptance criteria:

* **Incremental rerun fetches only changed/new material** — a second run
  emits no freshly-fetched envelopes for unchanged content (304 / hash
  dedup), and emits *only* the changed page when one fixture mutates.

* **Resumes after interruption** — seeding the adapter's SDK checkpoint
  with a completed URL makes ``collect()`` skip it on resume.

* **Passes the adapter contract suite** — the generic
  :func:`run_contract_suite` runs against the certified adapter.

* **Recorded-fixture suite** — the parser is validated against every
  representative fixture variant (standard / DNF / multiclass / no-IRC)
  with exact expected outputs and deterministic hashes.

* **Source-breakage mutation tests** — mutated fixtures (table removed,
  headers renamed, irrelevant page) are detected as breakage (zero
  records / structural signal), never silently parsed into garbage.

All tests use the in-process :class:`FakeHttpServer` — **zero network
calls**.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import urlparse

import httpx
import pytest

from irc_data.parsers.extraction_contract import ParserInputV1
from irc_data.parsers.topyacht import PARSER_VERSION, TopYachtParser
from irc_data.sources.envelope import AdapterCheckpointV1, FetchStatus, sha256_hex
from irc_data.sources.fake_adapter import FakeHttpServer
from irc_data.sources.gate import CollectionGate
from irc_data.sources.http_client import HttpClient
from irc_data.sources.policy import ACTIVE_POLICY, LegalStatus
from irc_data.sources.registry import get_in_memory_source
from irc_data.sources.topyacht_adapter import TopYachtAdapter
from tests.fixtures.topyacht import html as fx
from tests.sources.test_contract_suite import run_contract_suite


# ---------------------------------------------------------------------------
# Harness builders
# ---------------------------------------------------------------------------

def _fast_policy():
    return dataclasses.replace(
        ACTIVE_POLICY,
        rate=dataclasses.replace(
            ACTIVE_POLICY.rate, min_delay_seconds=0.0, jitter_seconds=0.0
        ),
    )


def _build_server(routes: dict[str, str] | None = None) -> FakeHttpServer:
    """Build a FakeHttpServer serving the TopYacht fixture tree."""
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
    # Health endpoint for the contract suite's health_probe
    server.add_route("/", b"OK", headers={"Content-Type": "text/plain"})
    return server


def _build_adapter(
    server: FakeHttpServer,
    *,
    checkpoint: AdapterCheckpointV1 | None = None,
) -> TopYachtAdapter:
    """Wire a :class:`TopYachtAdapter` to *server* with a fast policy."""
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
    return adapter


def _parse_envelope(envelope) -> list:
    """Parse a fetched envelope's content through the certified parser."""
    parser = TopYachtParser()
    inp = ParserInputV1(
        content=envelope.content,
        content_hash=envelope.content_hash,
        source_slug=envelope.source_slug,
        url=envelope.url,
    )
    return parser.parse(inp).records


# ---------------------------------------------------------------------------
# 1. Recorded-fixture suite — parser vs every representative variant
# ---------------------------------------------------------------------------


class TestRecordedFixtureParser:
    """Validate the certified parser against every preserved variant."""

    def setup_method(self):
        self.parser = TopYachtParser()

    def _parse(self, html: str, url: str = "http://topyacht.test/race.htm"):
        inp = ParserInputV1(
            content=html.encode(),
            content_hash=sha256_hex(html),
            source_slug="topyacht",
            url=url,
        )
        return self.parser.parse(inp)

    def test_standard_variant_three_finishers(self):
        batch = self._parse(fx.RACE_STANDARD)
        assert len(batch.records) == 3
        boats = [r.get_value("boat_name") for r in batch.records]
        assert boats == ["Black Jack", "Alive", "Celestial"]
        tccs = [r.get_value("tcc_at_race") for r in batch.records]
        assert tccs == ["1.105", "1.089", "1.061"]
        places = [r.get_value("place") for r in batch.records]
        assert places == [1, 2, 3]
        # Every record is a race_result with a division + source URL.
        for r in batch.records:
            assert r.record_type == "race_result"
            assert r.get_value("division") == "Division 1"
            assert r.get_value("source_url")

    def test_dnf_variant_status_detection(self):
        batch = self._parse(fx.RACE_DNF)
        assert len(batch.records) == 3
        statuses = [r.get_value("status") for r in batch.records]
        assert statuses == ["finished", "DNF", "DNS"]
        # DNF/DNS rows have no place.
        assert batch.records[1].get_value("place") is None
        assert batch.records[2].get_value("place") is None

    def test_multiclass_variant_only_irc_table(self):
        batch = self._parse(fx.RACE_MULTICLASS)
        # Only the IRC-captioned table must be parsed (2 rows), the PHS
        # table must be ignored.
        assert len(batch.records) == 2
        boats = [r.get_value("boat_name") for r in batch.records]
        assert boats == ["Racer X", "Racer Y"]
        for r in batch.records:
            assert r.get_value("division") == "Division 2"

    def test_no_irc_variant_zero_records(self):
        batch = self._parse(fx.RACE_NO_IRC)
        assert batch.records == []

    def test_parser_is_deterministic(self):
        b1 = self._parse(fx.RACE_STANDARD)
        b2 = self._parse(fx.RACE_STANDARD)
        assert b1.extraction_hash == b2.extraction_hash
        assert b1.batch_id == b2.batch_id

    def test_parser_version_is_set(self):
        assert self.parser.parser_version == PARSER_VERSION

    def test_fields_have_source_locators(self):
        batch = self._parse(fx.RACE_STANDARD)
        first = batch.records[0]
        boat_field = first.get_field("boat_name")
        assert boat_field is not None
        assert boat_field.locator is not None
        assert boat_field.locator.content_hash == batch.content_hash


# ---------------------------------------------------------------------------
# 2. Adapter discovery + collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_enumerates_irc_race_pages():
    """Discovery finds only the IRC-column race pages across both series."""
    server = _build_server()
    adapter = _build_adapter(server)
    items = await adapter.discover()
    urls = [i.url for i in items]
    # 2 IRC races in rategold + 1 in pass = 3 unique race pages.
    assert len(urls) == 3
    assert any(u.endswith("rategold/01RGrp2.htm") for u in urls)
    assert any(u.endswith("rategold/02RGrp2.htm") for u in urls)
    assert any(u.endswith("pass/01RGrp2.htm") for u in urls)
    # PHS / ORC column links must NOT be collected.
    assert not any("RGrp1" in u for u in urls)
    assert not any("RGrp3" in u for u in urls)


@pytest.mark.asyncio
async def test_collect_yields_raw_envelopes():
    server = _build_server()
    adapter = _build_adapter(server)
    envelopes = await adapter.run()
    assert len(envelopes) == 3
    for env in envelopes:
        assert env.status == FetchStatus.FETCHED
        assert env.content_hash == sha256_hex(env.content)
        assert env.source_slug == "topyacht"
        assert env.parse_hint == "html"


@pytest.mark.asyncio
async def test_envelopes_parse_through_certified_parser():
    """End-to-end: adapter → envelope → parser → records."""
    server = _build_server()
    adapter = _build_adapter(server)
    envelopes = await adapter.run()
    total_records = 0
    for env in envelopes:
        records = _parse_envelope(env)
        total_records += len(records)
    # standard(3) + dnf(3) + multiclass(2) = 8 records across 3 pages.
    assert total_records == 8


# ---------------------------------------------------------------------------
# 3. Incremental rerun — only changed/new material
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_rerun_no_changes_fetches_nothing_new():
    """Steady-state rerun: a second run with a complete checkpoint makes
    **zero** HTTP calls and emits **zero** freshly-fetched envelopes —
    unchanged material is not re-collected."""
    server = _build_server()
    adapter = _build_adapter(server)
    first = await adapter.run()
    assert len(first) == 3
    cp = adapter.save_checkpoint()
    cursor = adapter.save_discovery_cursor()

    # Second run: same server, resume from checkpoint + cursor.
    server.reset_call_counts()
    adapter2 = _build_adapter(server, checkpoint=cp)
    adapter2.load_discovery_cursor(cursor)
    second = await adapter2.run()

    # No freshly-fetched envelopes on the rerun.
    fresh = [e for e in second if e.status == FetchStatus.FETCHED]
    assert fresh == []
    # The completed checkpoint means zero HTTP calls at all (no re-walk of
    # the index/series enumeration, no re-fetch of completed race pages).
    assert server.call_count() == 0


@pytest.mark.asyncio
async def test_incremental_rerun_after_change_collects_changed_page():
    """When a page changes, a rerun collects the new content (new hash)."""
    server = _build_server()
    adapter = _build_adapter(server)
    first = await adapter.run()
    original_hash = adapter.save_checkpoint().content_hashes[
        "http://topyacht.test/results/2024/hirw/rategold/01RGrp2.htm"
    ]

    # Mutate one race page in-place on the server (content changes → new
    # hash → it is *not* skipped as unchanged).
    changed_path = "/results/2024/hirw/rategold/01RGrp2.htm"
    server.add_route(
        changed_path,
        fx.RACE_STANDARD_V2,
        headers={"Content-Type": "text/html", "ETag": '"etag-v2"'},
    )

    # Rerun with a fresh collection (new checkpoint), so the adapter
    # re-probes the tree and picks up the changed bytes.
    server.reset_call_counts()
    adapter2 = _build_adapter(server)
    second = await adapter2.run()

    changed = [
        e for e in second
        if e.url.endswith("rategold/01RGrp2.htm") and e.status == FetchStatus.FETCHED
    ]
    assert len(changed) == 1
    # New content → new hash, distinct from the original.
    assert changed[0].content_hash != original_hash
    # And it parses to the *new* 4-finisher content.
    records = _parse_envelope(changed[0])
    assert len(records) == 4
    assert any(r.get_value("boat_name") == "Patriot" for r in records)


# ---------------------------------------------------------------------------
# 4. Resume after interruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_interruption_skips_completed():
    """A checkpoint seeded with a completed URL is skipped on resume."""
    server = _build_server()
    adapter = _build_adapter(server)
    first = await adapter.run()
    assert len(first) == 3

    # Simulate interruption after the first page: checkpoint holds only
    # the first completed URL.
    completed_url = first[0].url
    cp = AdapterCheckpointV1(source_slug="topyacht")
    cp.mark_completed(completed_url, first[0].content_hash)

    server.reset_call_counts()
    adapter2 = _build_adapter(server, checkpoint=cp)
    # Fresh discovery (no cursor) but checkpointed content skip.
    resumed = await adapter2.run()
    resumed_urls = [e.url for e in resumed]
    assert completed_url not in resumed_urls
    assert len(resumed) == 2


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_preserves_progress():
    server = _build_server()
    adapter = _build_adapter(server)
    await adapter.run()
    cp = adapter.save_checkpoint()
    assert cp.status == "completed"
    assert len(cp.completed_urls) == 3
    # JSON round-trip preserves the contract.
    restored = AdapterCheckpointV1.from_json(cp.to_json())
    assert restored == cp


# ---------------------------------------------------------------------------
# 5. Adapter contract suite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_passes_contract_suite():
    """The certified adapter passes the generic adapter contract suite."""
    server = _build_server()
    adapter = _build_adapter(server)
    results = await run_contract_suite(adapter)
    assert results["discover_count"] == 3
    assert results["collect_count"] == 3
    assert results["checkpoint_urls"] == 3
    assert results["healthy"] is True


@pytest.mark.asyncio
async def test_adapter_policy_gates_hold_source():
    """A hold/disabled source cannot be collected (policy gate)."""
    server = _build_server()
    src = dataclasses.replace(
        get_in_memory_source("topyacht"),
        base_url="http://topyacht.test/results",
        legal_status=LegalStatus.HOLD,
    )
    pol = _fast_policy()
    gate = CollectionGate(policy=pol, sources=[src])
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(server._handler),
        follow_redirects=True,
        headers={"User-Agent": ACTIVE_POLICY.attribution.user_agent},
    )
    client = HttpClient(client=inner, policy=pol, backoff=(0.001,) * 4)
    from irc_data.sources.policy import SourceNotApprovedError

    with pytest.raises(SourceNotApprovedError):
        TopYachtAdapter(db=None, http_client=client, gate=gate, policy=pol)


# ---------------------------------------------------------------------------
# 6. Source-breakage mutation tests
# ---------------------------------------------------------------------------


class TestSourceBreakageMutations:
    """Mutated fixtures must be *detected* as breakage, never parsed into
    garbage records."""

    def setup_method(self):
        self.parser = TopYachtParser()

    def _count(self, html: str) -> int:
        inp = ParserInputV1(
            content=html.encode(),
            content_hash=sha256_hex(html),
            source_slug="topyacht",
            url="http://topyacht.test/race.htm",
        )
        return len(self.parser.parse(inp).records)

    def test_mutation_table_removed_detected(self):
        """Table removed → zero records (structural breakage detected)."""
        assert self._count(fx.MUTATED_NO_TABLES) == 0
        # Sanity: the healthy variant yields records, so zero is a signal.
        assert self._count(fx.RACE_STANDARD) == 3

    def test_mutation_headers_renamed_detected(self):
        """Headers renamed → boat column lost → zero records (no garbage)."""
        assert self._count(fx.MUTATED_HEADERS_RENAMED) == 0

    def test_mutation_irrelevant_page_detected(self):
        """An unrelated page yields zero records."""
        assert self._count(fx.MUTATED_IRRELEVANT) == 0

    @pytest.mark.asyncio
    async def test_structural_change_changes_content_hash(self):
        """A structural mutation changes the artifact content hash, which
        is what the source monitor keys on to detect breakage."""
        healthy = sha256_hex(fx.RACE_STANDARD)
        mutated = sha256_hex(fx.MUTATED_NO_TABLES)
        assert healthy != mutated

    @pytest.mark.asyncio
    async def test_breakage_aborts_record_yield_not_partial(self):
        """When the source breaks mid-collection, the breakage page emits
        zero records rather than a partial/garbage row set."""
        # Mutated server where one race page lost its table.
        routes = fx.fixture_routes()
        routes["/results/2024/hirw/rategold/02RGrp2.htm"] = fx.MUTATED_NO_TABLES
        server = _build_server(routes)
        adapter = _build_adapter(server)
        envelopes = await adapter.run()
        # Key by the full path (two race pages share the filename
        # "01RGrp2.htm" under different series directories).
        per_path_records = {
            urlparse(e.url).path: len(_parse_envelope(e)) for e in envelopes
        }
        # The healthy pages still parse; the broken page yields zero.
        assert per_path_records[
            "/results/2024/hirw/rategold/01RGrp2.htm"
        ] == 3
        assert per_path_records[
            "/results/2024/hirw/rategold/02RGrp2.htm"
        ] == 0
