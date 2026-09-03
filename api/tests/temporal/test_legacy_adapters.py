"""OPS-02-04 — contract tests for the legacy-scraper adapter registry.

Every legacy CLI scraper (orc, tcc, sailsys, topyacht, isora, rhkyc,
sailracehq, cert discovery/parse, wayback) is registered as an adapter the
OPS-01-02 ``SourceRunWorkflow`` can run.  These tests pin the contract:

* each of the issue's legacy scrapers is registered under a
  ``data_sources`` register slug;
* ``argv()`` is a pure, non-empty legacy CLI invocation;
* ``run(record)`` returns a JSON-able mapping that always carries
  ``records_written: int`` (runners are stubbed — the contract is about the
  adapter surface, not the network);
* the workflow's ``run_registered_adapter`` activity routes through the
  registry (legacy first, ``adapter_class`` fallback, ledger-only last);
* every run dual-writes ``source_runs`` **and** ``ingestion_log`` (until the
  admin reads ``source_runs`` — disable via ``SOURCE_RUNS_DUAL_WRITE=0``).

Live verification (real Temporal + Postgres) lives in
``test_legacy_source_lifecycle.py``.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.temporal import legacy_adapters
from irc_data.temporal.legacy_adapters import (
    LEGACY_CLI_ADAPTERS,
    LegacyScraperAdapter,
    adapter_for_slug,
    registered_slugs,
)

# The legacy scrapers named in the OPS-02-04 scope and the register slug that
# fronts each of them.
EXPECTED_LEGACY_SLUGS = {
    "orc",            # scrape orc
    "irc-tcc",        # scrape tcc
    "sailsys",        # scrape results --source sailsys --all-clubs
    "topyacht",       # scrape results --source topyacht --incremental --store
    "isora",          # scrape results --source isora
    "rhkyc",          # scrape results --source rhkyc
    "sailracehq",     # scrape results --source sailracehq
    "irc-certs",      # cert discovery (scrape certs --exhaustive) + parse-certs
    "wayback-irc",    # scrape wayback / wayback-tcc
}


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


class TestRegistryContract:
    def test_every_legacy_scraper_is_registered(self):
        assert EXPECTED_LEGACY_SLUGS <= set(registered_slugs())

    def test_registered_slugs_are_sorted_and_unique(self):
        slugs = registered_slugs()
        assert slugs == sorted(set(slugs))

    @pytest.mark.parametrize("slug", sorted(EXPECTED_LEGACY_SLUGS))
    def test_adapter_matches_register_row(self, slug):
        """The adapter's slug resolves to a real data_sources register row."""
        adapter = adapter_for_slug(slug)
        assert adapter is not None
        assert adapter.slug == slug
        # The seed register (source of truth for data_sources rows) carries
        # this slug — i.e. the adapter is schedulable via the register.
        from irc_data.sources.seed_data import SEED_SOURCES

        seed_slugs = {r.slug for r in SEED_SOURCES}
        assert slug in seed_slugs, f"{slug} has an adapter but no register row"

    @pytest.mark.parametrize("slug", sorted(EXPECTED_LEGACY_SLUGS))
    def test_argv_is_a_pure_legacy_cli_invocation(self, slug):
        adapter = adapter_for_slug(slug)
        argv = adapter.argv()
        assert isinstance(argv, list) and argv, "argv must be a non-empty list"
        assert all(isinstance(a, str) for a in argv)
        # argv is the legacy CLI invocation minus the irc-data executable
        assert argv[0] in {"scrape", "scrape-news", "wayback-tcc", "parse-certs"}
        # pure: calling twice returns equal, independent lists
        argv2 = adapter.argv()
        assert argv == argv2 and argv is not argv2

    def test_wayback_has_a_tcc_mode(self):
        """The wayback adapter covers both cert discovery and TCC harvest."""
        certs = adapter_for_slug("wayback-irc")
        tcc = adapter_for_slug("wayback-irc", mode="tcc")
        assert certs is not None and tcc is not None
        assert certs.argv() == ["scrape", "wayback"]
        assert tcc.argv() == ["wayback-tcc"]

    def test_irc_certs_chains_discovery_and_parse(self):
        """The irc-certs adapter documents both halves of the legacy job."""
        adapter = adapter_for_slug("irc-certs")
        assert "certs" in adapter.cli_argv  # scrape certs --exhaustive
        assert "discovery" in adapter.description.lower()
        assert "parse" in adapter.description.lower()

    def test_unknown_slug_maps_to_none(self):
        assert adapter_for_slug("no-such-source") is None


# ---------------------------------------------------------------------------
# Run contract — stubbed runners (hermetic)
# ---------------------------------------------------------------------------


class TestRunContract:
    @pytest.mark.parametrize("slug", sorted(EXPECTED_LEGACY_SLUGS))
    def test_run_returns_jsonable_mapping_with_records_written(self, slug):
        adapter = adapter_for_slug(slug)
        stub = LegacyScraperAdapter(
            slug=adapter.slug,
            cli_argv=adapter.cli_argv,
            runner=lambda record: {"records_written": 5, "note": "stub"},
        )
        out = stub.run({"slug": slug})
        assert out["records_written"] == 5
        assert out["adapter"] == f"legacy-cli:{slug}"
        assert "elapsed_seconds" in out
        json.dumps(out)  # JSON-able

    def test_run_normalises_missing_records_written(self):
        adapter = LegacyScraperAdapter(
            slug="orc", cli_argv=("scrape", "orc"), runner=lambda record: {}
        )
        assert adapter.run({})["records_written"] == 0

    def test_run_normalises_non_mapping_output(self):
        adapter = LegacyScraperAdapter(
            slug="orc", cli_argv=("scrape", "orc"), runner=lambda record: "done"
        )
        out = adapter.run({})
        assert out["records_written"] == 0
        assert out["adapter_output"] == "done"

    def test_run_propagates_failures(self):
        def _boom(record):
            raise RuntimeError("scraper exploded")

        adapter = LegacyScraperAdapter(
            slug="orc", cli_argv=("scrape", "orc"), runner=_boom
        )
        with pytest.raises(RuntimeError, match="scraper exploded"):
            adapter.run({})


# ---------------------------------------------------------------------------
# Dispatch contract — the workflow activity routes through the registry
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.asyncio
    async def test_legacy_registry_takes_precedence(self, monkeypatch):
        """A register slug in the legacy registry runs its legacy adapter."""
        from irc_data.temporal.ledger import activities as ledger_activities

        calls = []

        async def _fake_run_legacy_source(record):
            calls.append(record)
            return {"records_written": 7, "adapter": "legacy-cli:orc"}

        monkeypatch.setattr(
            legacy_adapters, "run_legacy_source", _fake_run_legacy_source
        )

        out = await ledger_activities.run_registered_adapter(
            {"slug": "orc", "base_url": "https://data.orc.org"}, "rk-1"
        )
        assert out["records_written"] == 7
        assert calls and calls[0]["slug"] == "orc"

    @pytest.mark.asyncio
    async def test_unmapped_slug_records_ledger_only_run(self, monkeypatch):
        from irc_data.temporal.ledger import activities as ledger_activities

        async def _none(record):
            return None

        monkeypatch.setattr(legacy_adapters, "run_legacy_source", _none)
        out = await ledger_activities.run_registered_adapter(
            {"slug": "cbh", "base_url": None}, "rk-2"
        )
        assert out["records_written"] == 0
        assert out["adapter"] == "none"

    @pytest.mark.asyncio
    async def test_adapter_class_fallback(self):
        """DP-01 SDK adapters resolve via adapter_class when not legacy-mapped."""
        record = {"slug": "unmapped", "adapter_class": f"{__name__}._sdk_adapter"}
        out = await legacy_adapters.run_legacy_source(record)
        assert out == {"records_written": 2, "via": "adapter_class"}

    @pytest.mark.asyncio
    async def test_adapter_class_fallback_async_callable(self):
        record = {"slug": "unmapped", "adapter_class": f"{__name__}._sdk_adapter_async"}
        out = await legacy_adapters.run_legacy_source(record)
        assert out == {"records_written": 3, "via": "adapter_class_async"}


def _sdk_adapter(record):
    return {"records_written": 2, "via": "adapter_class"}


async def _sdk_adapter_async(record):
    return {"records_written": 3, "via": "adapter_class_async"}


# ---------------------------------------------------------------------------
# Dual-write contract — source_runs ⟺ ingestion_log
# ---------------------------------------------------------------------------


@pytest.fixture()
def ledger_engine():
    """SQLite engine with the source_runs + ingestion_log tables."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE ingestion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status TEXT DEFAULT 'running',
                    records_found INTEGER,
                    records_new INTEGER,
                    records_updated INTEGER,
                    error_message TEXT,
                    metadata TEXT
                )
                """
            )
        )
    return eng


class TestDualWrite:
    def test_open_mirrors_ingestion_log_rows(self, ledger_engine, monkeypatch):
        monkeypatch.setenv(legacy_adapters.DUAL_WRITE_ENV, "1")
        ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            ledger_engine, "orc", run_key="rk-open", trigger="manual"
        )
        assert ids == {"orc": ids["orc"]}
        with ledger_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT source, status, metadata FROM ingestion_log")
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["source"] == "orc"
        assert rows[0]["status"] == "running"
        assert json.loads(rows[0]["metadata"])["run_key"] == "rk-open"

    def test_close_completes_the_mirror(self, ledger_engine, monkeypatch):
        monkeypatch.setenv(legacy_adapters.DUAL_WRITE_ENV, "1")
        ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            ledger_engine, "sailsys", run_key="rk-close"
        )
        legacy_adapters.mirror_run_close_to_ingestion_log(
            ledger_engine,
            "sailsys",
            run_key="rk-close",
            status="success",
            stats={"records_found": 12, "records_written": 3},
            log_ids=ids,
        )
        with ledger_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, records_found, records_new, completed_at "
                    "FROM ingestion_log WHERE id = :i"
                ),
                {"i": ids["sailsys"]},
            ).mappings().one()
        assert row["status"] == "completed"
        assert row["records_found"] == 12
        assert row["records_new"] == 3
        assert row["completed_at"] is not None

    def test_close_maps_failure_and_error(self, ledger_engine, monkeypatch):
        monkeypatch.setenv(legacy_adapters.DUAL_WRITE_ENV, "1")
        ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            ledger_engine, "topyacht", run_key="rk-fail"
        )
        legacy_adapters.mirror_run_close_to_ingestion_log(
            ledger_engine,
            "topyacht",
            run_key="rk-fail",
            status="failed",
            detail="boom",
            stats={},
            log_ids=ids,
        )
        with ledger_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, error_message FROM ingestion_log WHERE id = :i"
                ),
                {"i": ids["topyacht"]},
            ).mappings().one()
        assert row["status"] == "failed"
        assert row["error_message"] == "boom"

    def test_dual_write_can_be_disabled(self, ledger_engine, monkeypatch):
        """SOURCE_RUNS_DUAL_WRITE=0 — admin now reads source_runs directly."""
        monkeypatch.setenv(legacy_adapters.DUAL_WRITE_ENV, "0")
        ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            ledger_engine, "orc", run_key="rk-off"
        )
        assert ids == {}
        with ledger_engine.connect() as conn:
            n = conn.execute(text("SELECT count(*) FROM ingestion_log")).scalar()
        assert n == 0

    def test_alias_sources_get_one_row_each(self, ledger_engine, monkeypatch):
        """irc-tcc mirrors to its historical ingestion_log source name too."""
        monkeypatch.setenv(legacy_adapters.DUAL_WRITE_ENV, "1")
        ids = legacy_adapters.mirror_run_open_to_ingestion_log(
            ledger_engine, "irc-tcc", run_key="rk-alias"
        )
        assert set(ids) == {"irc_tcc", "irc-tcc"}
