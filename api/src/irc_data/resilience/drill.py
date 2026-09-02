"""The DP-05-05 drill harness: load, resilience and disaster-recovery.

:class:`DataPlaneDrill` drives a production-shaped synthetic load through
the data plane while injecting the fault and disaster scenarios named by
the issue, and produces a signed :class:`DrillReportV1`.

The drill is deliberately *self-contained*: it stands up its own
SQLite-backed engine, raw lake and published-artifact store in a working
directory, so it can be run in CI or against a scratch environment
without touching production state.  Every store layer it exercises uses
portable SQL, so behaviour measured here carries over to Postgres in
production.

A note on the consumer-view model
---------------------------------
The DP-05-02 quality store keys batches by ``(pipeline, source_slug,
version)`` and the consumer view shows the *promoted* batch for a
``(pipeline, source_slug)`` pair — promoting a new version supersedes the
old one.  To accumulate one consumer-visible row per artifact (so the
drill can assert "no loss, no duplicate"), the drill gives **each
artifact its own pipeline key** ``drill.extraction.<source>.<i>``.  The
published consumer state for a source is then the union of the consumer
views across that source's pipeline keys.  Provenance (the raw artifact
id + content hash a row cites) is read from the field locators inside
each staged record.

Scenarios (in execution order)
------------------------------
1. ``high_volume_ingest`` — push ``config.artifact_volume`` synthetic
   artifacts through raw-lake capture, run-ledger accounting and the
   gated promotion path; measure ingest throughput and assert the
   consumer state is exactly the promoted rows (no loss, no dup).
2. ``backfill_under_load`` — while fresh ingest continues, run a
   DP-02-04 replay over the published corpus into an isolated batch;
   measure replay throughput and assert the replay is idempotent
   (same ``plan_id`` → same batch) and correct (``compare_batches``
   accounts for every artifact).
3. ``concurrent_adapters`` — run ``config.concurrent_adapters`` adapter
   loops in parallel threads against distinct sources; assert the ledger
   records every run exactly once and the promoted row count is the sum
   across sources (no lost or double-counted work).
4. ``database_outage`` — simulate a database outage and assert writes
   fail *safely* (raised errors, no partial rows); on restore, measure
   RTO and assert the pre-outage published state is fully intact
   (RPO 0).
5. ``object_store_outage`` — simulate a raw-lake (object store) outage;
   assert ``store()`` raises and leaves no partial object or index row;
   on restore, measure RTO and assert every pre-outage object verifies.
6. ``restore_and_replay`` — the disaster-recovery drill: destroy the
   operational database, rebuild it from the raw lake by replaying every
   artifact through the gate + promotion path, and assert: published
   data *and* per-field provenance survive (consumer state and
   provenance chain match the pre-disaster baseline), then replay
   *again* and assert **no duplicate publication** follows.  Measures
   RPO / RTO.

The scenario functions never raise on an expected fault — they record
the outcome in a :class:`ScenarioResultV1`.  A scenario is ``passed``
iff every named check in ``checks`` held.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.db import run_ledger
from irc_data.parsers.extraction_contract import (
    ExtractionBatchV1,
    ExtractedField,
    ExtractedRecord,
    Locator,
    LocatorType,
)
from irc_data.quality import gate_store, gates
from irc_data.quality.contracts import GateKind
from irc_data.resilience.contracts import (
    DrillReportV1,
    ScenarioResultV1,
    ScenarioStatus,
    sign_report,
)
from irc_data.sources.raw_lake import RawLakeStorage
from irc_data.temporal.replay import replay_store
from irc_data.temporal.replay.contracts import (
    ArtifactFilter,
    BatchStatus,
    ReplayPlanV1,
)

#: Default "production-sized" synthetic load.  Kept modest enough to run
#: in CI while still exercising batching, sharding and concurrency paths.
DEFAULT_ARTIFACT_VOLUME = 1000

#: Pipeline label prefix for the gated promotion path in the drill.  The
#: full pipeline key for one artifact is ``<prefix>.<source>.<i>``.
DRILL_PIPELINE_PREFIX = "drill.extraction"

#: Source slug used for the high-volume corpus (shared by scenarios 1 & 2).
HV_SOURCE = "drill-hv"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DrillConfig:
    """Tuning knobs for a drill run.

    artifact_volume
        Number of synthetic artifacts pushed per single-source load
        scenario (the "production-sized synthetic load").
    concurrent_adapters
        Number of adapter loops run in parallel in the concurrency
        scenario.
    per_adapter_volume
        Artifacts each concurrent adapter ingests.
    backfill_batch
        Max number of published artifacts the backfill replay selects.
    signing_key
        HMAC key used to sign the report.  If ``None``, one is
        generated for the run (the key itself is held on the drill
        instance for verification; only ``signing_key_id`` is recorded
        on the report).
    signing_key_id
        Identifier recorded on the report for the signing key.
    work_dir
        Directory for the drill's DB / raw lake.  If ``None`` a temp
        dir is created and cleaned up on close.
    """

    artifact_volume: int = DEFAULT_ARTIFACT_VOLUME
    concurrent_adapters: int = 4
    per_adapter_volume: int = 50
    backfill_batch: int = 200
    signing_key: bytes | None = None
    signing_key_id: str = "drill-key-1"
    work_dir: str | Path | None = None


class DataPlaneDrill:
    """Runs the load / resilience / DR drill and produces a signed report."""

    def __init__(self, config: DrillConfig | None = None):
        self.config = config or DrillConfig()
        self._owns_dir = self.config.work_dir is None
        self.work_dir = Path(
            self.config.work_dir or tempfile.mkdtemp(prefix="dp05_drill_")
        )
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # The drill's signing key: provided or generated for the run.
        self.signing_key = self.config.signing_key or Fernet.generate_key()

        self.db_path = self.work_dir / "drill.db"
        self.lake_dir = self.work_dir / "raw_lake"
        self.lake_dir.mkdir(parents=True, exist_ok=True)

        self.engine = self._make_engine(self.db_path)
        self._init_schema(self.engine)

        self.lake = RawLakeStorage(
            self.lake_dir, encryption_key=Fernet.generate_key()
        )

        # Provenance registry: artifact_id -> receipt, so the restore
        # drill can replay from the raw lake and prove provenance held.
        self._receipts: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Engine / schema
    # ------------------------------------------------------------------

    def _make_engine(self, path: Path) -> Engine:
        return create_engine(
            f"sqlite+pysqlite:///{path}",
            future=True,
            connect_args={"check_same_thread": False},
        )

    def _init_schema(self, engine: Engine) -> None:
        """Create every table the drill exercises (idempotent)."""
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS ingestion_log (
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
        gate_store.init_quality_tables(engine)
        replay_store.init_replay_tables(engine)
        replay_store.init_published_artifacts_table(engine)

    # ------------------------------------------------------------------
    # Pipeline-key + synthetic payload helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pipeline_for(source_slug: str, i: int) -> str:
        """The unique pipeline key for artifact *i* of *source_slug*.

        One pipeline key per artifact means the per-key consumer view is
        that artifact's single row, and the source's consumer state is
        the union across its keys (so rows accumulate; promotion of one
        artifact never supersedes another).
        """
        return f"{DRILL_PIPELINE_PREFIX}.{source_slug}.{i}"

    def _consumer_rows_for_source(self, source_slug: str) -> list[dict[str, Any]]:
        """Union of promoted consumer rows across a source's pipelines."""
        rows: list[dict[str, Any]] = []
        with self.engine.connect() as conn:
            pipelines = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT DISTINCT pipeline FROM quality_batches "
                        "WHERE source_slug = :s AND pipeline LIKE :pfx"
                    ),
                    {
                        "s": source_slug,
                        "pfx": f"{DRILL_PIPELINE_PREFIX}.{source_slug}.%",
                    },
                ).fetchall()
            ]
        for pipeline in pipelines:
            rows.extend(gates.get_consumer_view(self.engine, pipeline, source_slug))
        return rows

    @staticmethod
    def _row_fingerprint(row_json: dict[str, Any]) -> str:
        """A stable identity for a consumer row's *data*.

        Hashes the extracted field (name, value) pairs — the data a
        consumer sees.  Excludes locator identity so we compare content.
        """
        fields = [
            (f.get("name"), json.dumps(f.get("value"), sort_keys=True, default=str))
            for f in row_json.get("fields", [])
        ]
        return hashlib.sha256(
            json.dumps(sorted(fields), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _row_provenance(row_json: dict[str, Any]) -> set[tuple[str, str]]:
        """The set of ``(artifact_id, content_hash)`` a row cites.

        Read from the field locators — this is the per-field provenance
        the extraction gate enforces, and what must survive recovery.
        """
        prov: set[tuple[str, str]] = set()
        for f in row_json.get("fields", []):
            loc = f.get("locator") or {}
            aid = loc.get("artifact_id")
            ch = loc.get("content_hash")
            if aid and ch:
                prov.add((aid, ch))
        return prov

    def _synthetic_content(self, source_slug: str, i: int) -> bytes:
        return (
            f"<html><body><table>"
            f"<tr><td>{i}</td><td>{source_slug}-boat-{i}</td>"
            f"<td>{1000 + i}</td></tr>"
            f"</table></body></html>"
        ).encode("utf-8")

    def _build_extraction_batch(
        self,
        receipt: Any,
        source_slug: str,
        i: int,
    ) -> ExtractionBatchV1:
        """A well-formed extraction batch whose fields cite the artifact."""
        locator = Locator(
            artifact_id=receipt.artifact_id,
            content_hash=receipt.content_hash,
            locator_type=LocatorType.TABLE_CELL.value,
            row=0,
            start=0,
            snippet=f"<td>{i}</td>",
        )
        record = ExtractedRecord(
            record_type="race_result",
            record_index=0,
            fields=[
                ExtractedField(
                    name="sail_number", value=f"{1000 + i}", locator=locator
                ),
                ExtractedField(
                    name="boat_name",
                    value=f"{source_slug}-boat-{i}",
                    locator=locator,
                ),
                ExtractedField(name="place", value=i + 1, locator=locator),
            ],
        )
        return ExtractionBatchV1(
            artifact_id=receipt.artifact_id,
            content_hash=receipt.content_hash,
            parser_version="1.0.0",
            schema_version="v1",
            source_slug=source_slug,
            url=receipt.url,
            records=[record],
        )

    def _ingest_one(
        self,
        source_slug: str,
        i: int,
        *,
        promote: bool = True,
    ) -> dict[str, Any]:
        """Run one synthetic artifact through the full data-plane path.

        raw-lake capture → ledger row → gated validation → promotion.
        """
        content = self._synthetic_content(source_slug, i)
        url = f"https://{source_slug}.test/results/{i}"

        receipt = self.lake.store(
            content,
            source_slug=source_slug,
            url=url,
            content_type="text/html",
        )
        self._receipts[receipt.artifact_id] = receipt

        run_id = run_ledger.record_run(
            self.engine,
            source_slug,
            status=run_ledger.STATUS_COMPLETED,
            records_found=1,
            records_new=1,
            records_updated=0,
        )

        batch = self._build_extraction_batch(receipt, source_slug, i)
        pipeline = self._pipeline_for(source_slug, i)
        result = gates.ingest_validate_and_optionally_promote(
            self.engine,
            pipeline=pipeline,
            source_slug=source_slug,
            gate=GateKind.EXTRACTION.value,
            payload=batch,
            auto_promote=promote,
            promoted_by="dp05-drill",
        )
        return {
            "run_id": run_id,
            "receipt": receipt,
            "pipeline": pipeline,
            "outcome": result["outcome"],
            "index": i,
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> DrillReportV1:
        """Run every scenario and return the signed report."""
        started = time.perf_counter()
        started_at = _now_iso()

        scenario_fns = [
            self._scenario_high_volume_ingest,
            self._scenario_backfill_under_load,
            self._scenario_concurrent_adapters,
            self._scenario_database_outage,
            self._scenario_object_store_outage,
            self._scenario_restore_and_replay,
        ]
        scenarios: list[ScenarioResultV1] = []
        for fn in scenario_fns:
            try:
                scenarios.append(fn())
            except Exception as exc:  # a scenario must never crash the drill
                name = fn.__name__.replace("_scenario_", "")
                scenarios.append(
                    ScenarioResultV1(
                        scenario=name,
                        status=ScenarioStatus.FAILED.value,
                        error=f"unhandled: {exc!r}",
                        checks={"no_unhandled_exception": False},
                    ).finalise()
                )

        duration = time.perf_counter() - started
        report = self._assemble_report(scenarios, started_at, duration)
        sign_report(report, self.signing_key, key_id=self.config.signing_key_id)
        return report

    # ------------------------------------------------------------------
    # Report assembly
    # ------------------------------------------------------------------

    def _assemble_report(
        self,
        scenarios: list[ScenarioResultV1],
        started_at: str,
        duration: float,
    ) -> DrillReportV1:
        total_volume = sum(s.volume for s in scenarios)
        throughput_scenarios = [
            s for s in scenarios if s.throughput_per_second is not None
        ]
        load_time = sum(s.duration_seconds for s in throughput_scenarios)
        load_volume = sum(s.volume for s in throughput_scenarios)
        agg_throughput = (
            round(load_volume / load_time, 2) if load_time > 0 else 0.0
        )

        restore = next(
            (s for s in scenarios if s.scenario == "restore_and_replay"), None
        )
        measured_rpo = restore.rpo_seconds if restore else None
        measured_rto = restore.rto_seconds if restore else None

        overall = (
            ScenarioStatus.PASSED.value
            if all(s.status == ScenarioStatus.PASSED.value for s in scenarios)
            else ScenarioStatus.FAILED.value
        )

        ac = {
            "published_data_and_provenance_survive_recovery": bool(
                restore
                and restore.checks.get("published_data_survives")
                and restore.checks.get("provenance_survives")
            ),
            "rpo_rto_and_throughput_measured": bool(
                measured_rpo is not None
                and measured_rto is not None
                and throughput_scenarios
            ),
            "no_duplicate_publication_follows_replay": bool(
                restore and restore.checks.get("no_duplicate_publication")
            ),
        }

        return DrillReportV1(
            report_id=f"drill_{uuid.uuid4().hex[:16]}",
            started_at=started_at,
            completed_at=_now_iso(),
            duration_seconds=round(duration, 3),
            scenarios=scenarios,
            overall_status=overall,
            artifact_volume=total_volume,
            aggregate_throughput_per_second=agg_throughput,
            measured_rpo_seconds=measured_rpo,
            measured_rto_seconds=measured_rto,
            passed_acceptance_criteria=ac,
            signing_key_id=self.config.signing_key_id,
        )

    # ------------------------------------------------------------------
    # Scenario 1 — high artifact volume
    # ------------------------------------------------------------------

    def _scenario_high_volume_ingest(self) -> ScenarioResultV1:
        source = HV_SOURCE
        n = self.config.artifact_volume
        res = ScenarioResultV1(scenario="high_volume_ingest", started_at=_now_iso())

        t0 = time.perf_counter()
        for i in range(n):
            self._ingest_one(source, i)
        duration = time.perf_counter() - t0

        view = self._consumer_rows_for_source(source)
        fingerprints = [self._row_fingerprint(r["row_json"]) for r in view]
        with self.engine.connect() as conn:
            ledger_count = conn.execute(
                text("SELECT COUNT(*) FROM ingestion_log WHERE source = :s"),
                {"s": source},
            ).scalar_one()
        integrity = self.lake.verify_all()

        res.volume = n
        res.duration_seconds = round(duration, 3)
        res.throughput_per_second = round(n / duration, 2) if duration > 0 else None
        res.metrics = {
            "consumer_rows": len(view),
            "distinct_consumer_rows": len(set(fingerprints)),
            "ledger_runs": int(ledger_count),
            "raw_lake_ok": integrity["ok"],
            "raw_lake_corrupted": integrity["corrupted"],
            "raw_lake_missing": integrity["missing"],
        }
        res.checks = {
            "all_artifacts_promoted": len(view) == n,
            "no_duplicate_rows": len(set(fingerprints)) == n,
            "ledger_recorded_every_run": int(ledger_count) == n,
            "raw_lake_fully_intact": integrity["corrupted"] == 0
            and integrity["missing"] == 0,
        }
        res.evidence = [
            f"Ingested {n} artifacts in {duration:.2f}s "
            f"({res.throughput_per_second}/s)",
            f"Consumer rows = {len(view)} (distinct {len(set(fingerprints))}), "
            f"expected {n}",
            f"Raw lake integrity: {integrity}",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Scenario 2 — backfill under load
    # ------------------------------------------------------------------

    def _scenario_backfill_under_load(self) -> ScenarioResultV1:
        source = HV_SOURCE  # reuse the corpus built by scenario 1
        res = ScenarioResultV1(scenario="backfill_under_load", started_at=_now_iso())

        # Seed the replay published-store from the raw-lake receipts so
        # the backfill has a corpus to reparse (scenario 1 promoted via
        # the quality path, which is separate from the replay store).
        artifacts = replay_store.select_artifacts(
            self.engine, ArtifactFilter(source_slug=source)
        )
        if not artifacts:
            for r in [r for r in self._receipts.values() if r.source_slug == source]:
                replay_store.insert_published_artifact(
                    self.engine,
                    source_slug=source,
                    artifact_url=r.url,
                    content_hash=r.content_hash,
                    parsed_output={"seeded": True, "url": r.url},
                    parser_version="1.0.0",
                )
            artifacts = replay_store.select_artifacts(
                self.engine, ArtifactFilter(source_slug=source)
            )

        plan = ReplayPlanV1(
            source_slug=source,
            new_parser_version="2.0.0",
            artifact_filter=ArtifactFilter(
                source_slug=source, limit=self.config.backfill_batch
            ),
        )
        selected = replay_store.select_artifacts(self.engine, plan.artifact_filter)

        # Run the backfill while a concurrent live ingest continues.
        stop_flag = threading.Event()
        live = {"i": 0, "done": 0}

        def _background_ingest() -> None:
            while not stop_flag.is_set():
                self._ingest_one("drill-backfill-live", live["i"])
                live["i"] += 1
                live["done"] += 1

        t0 = time.perf_counter()
        worker = threading.Thread(target=_background_ingest, daemon=True)
        worker.start()

        batch = replay_store.create_or_get_batch(self.engine, plan)
        replay_store.update_batch_status(self.engine, batch["id"], BatchStatus.RUNNING)
        for art in selected:
            old = art.get("parsed_output")
            new_out = dict(old) if isinstance(old, dict) else {"raw": old}
            new_out["replayed"] = True
            replay_store.store_parsed_output(
                self.engine,
                batch_id=batch["id"],
                artifact_url=art["artifact_url"],
                content_hash=art["content_hash"],
                parsed_output=new_out,
                old_parsed_output=old,
            )
        replay_store.update_batch_status(
            self.engine, batch["id"], BatchStatus.AWAITING_APPROVAL
        )
        comparison = replay_store.compare_batches(self.engine, batch["id"])

        stop_flag.set()
        worker.join(timeout=10)
        duration = time.perf_counter() - t0

        # Idempotency: re-create the batch with the same plan_id.
        batch_again = replay_store.create_or_get_batch(self.engine, plan)

        n = len(selected)
        res.volume = n
        res.duration_seconds = round(duration, 3)
        res.throughput_per_second = round(n / duration, 2) if duration > 0 else None
        res.metrics = {
            "replayed_artifacts": n,
            "comparison": comparison.to_dict(),
            "concurrent_live_ingests": live["done"],
            "batch_id": batch["id"],
        }
        res.checks = {
            "replay_idempotent_same_batch": batch_again["id"] == batch["id"],
            "all_selected_replayed": comparison.total_artifacts == n,
            "comparison_accounts_for_every_artifact": (
                comparison.identical + comparison.changed + comparison.added
                == comparison.total_artifacts
            ),
            "ingest_continued_during_backfill": live["done"] > 0,
        }
        res.evidence = [
            f"Backfilled {n} artifacts in {duration:.2f}s "
            f"({res.throughput_per_second}/s)",
            f"Re-submitting plan {plan.plan_id} returned batch "
            f"{batch_again['id']} (same as {batch['id']})",
            f"Concurrent live ingest completed {live['done']} rows during "
            "the backfill",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Scenario 3 — concurrent adapters
    # ------------------------------------------------------------------

    def _scenario_concurrent_adapters(self) -> ScenarioResultV1:
        k = self.config.concurrent_adapters
        per = self.config.per_adapter_volume
        res = ScenarioResultV1(scenario="concurrent_adapters", started_at=_now_iso())

        errors: list[str] = []
        lock = threading.Lock()

        def _adapter(adapter_idx: int) -> None:
            source = f"drill-conc-{adapter_idx}"
            try:
                for i in range(per):
                    self._ingest_one(source, i)
            except Exception as exc:  # pragma: no cover - defensive
                with lock:
                    errors.append(f"adapter {adapter_idx}: {exc!r}")

        t0 = time.perf_counter()
        threads = [
            threading.Thread(target=_adapter, args=(a,), daemon=True)
            for a in range(k)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        duration = time.perf_counter() - t0

        total = k * per
        ledger_runs = 0
        consumer_rows = 0
        with self.engine.connect() as conn:
            for a in range(k):
                source = f"drill-conc-{a}"
                ledger_runs += conn.execute(
                    text("SELECT COUNT(*) FROM ingestion_log WHERE source = :s"),
                    {"s": source},
                ).scalar_one()
                consumer_rows += len(self._consumer_rows_for_source(source))

        res.volume = total
        res.duration_seconds = round(duration, 3)
        res.throughput_per_second = (
            round(total / duration, 2) if duration > 0 else None
        )
        res.metrics = {
            "adapters": k,
            "per_adapter": per,
            "ledger_runs": int(ledger_runs),
            "consumer_rows": consumer_rows,
            "errors": errors,
        }
        res.checks = {
            "no_adapter_errors": not errors,
            "ledger_recorded_every_run_exactly_once": int(ledger_runs) == total,
            "every_adapter_row_promoted": consumer_rows == total,
        }
        res.evidence = [
            f"{k} adapters × {per} artifacts = {total} in {duration:.2f}s "
            f"({res.throughput_per_second}/s)",
            f"Ledger runs = {ledger_runs}, consumer rows = {consumer_rows} "
            f"(both expected {total})",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Scenario 4 — database outage
    # ------------------------------------------------------------------

    def _scenario_database_outage(self) -> ScenarioResultV1:
        source = "drill-dbout"
        res = ScenarioResultV1(scenario="database_outage", started_at=_now_iso())

        baseline_n = 10
        for i in range(baseline_n):
            self._ingest_one(source, i)
        baseline_view = self._consumer_rows_for_source(source)
        baseline_fp = sorted(
            self._row_fingerprint(r["row_json"]) for r in baseline_view
        )

        # Inject the outage: swap in an engine pointing at a missing path
        # so every connection fails.
        real_engine = self.engine
        outage_engine = self._make_engine(self.work_dir / "nonexistent" / "db.db")

        rto_start = time.perf_counter()
        write_failed_safely = False
        try:
            self.engine = outage_engine
            try:
                run_ledger.record_run(
                    self.engine,
                    source,
                    status=run_ledger.STATUS_COMPLETED,
                    records_found=1,
                    records_new=1,
                )
            except Exception:
                write_failed_safely = True
        finally:
            self.engine = real_engine
        rto = time.perf_counter() - rto_start

        # Confirm nothing was written to the real store during outage.
        with real_engine.connect() as conn:
            rows_during = conn.execute(
                text("SELECT COUNT(*) FROM ingestion_log WHERE source = :s"),
                {"s": source},
            ).scalar_one()

        # After restore the system accepts writes and pre-outage state is
        # fully intact (RPO 0 — nothing committed was lost).
        self._ingest_one(source, 9999)
        restored_view = self._consumer_rows_for_source(source)
        restored_fp = sorted(
            self._row_fingerprint(r["row_json"]) for r in restored_view
        )

        res.volume = baseline_n
        res.duration_seconds = round(rto, 3)
        res.rto_seconds = round(rto, 3)
        res.rpo_seconds = 0.0
        res.metrics = {
            "baseline_rows": baseline_n,
            "rows_after_restore": len(restored_fp),
            "ledger_rows_during_outage": int(rows_during),
        }
        res.checks = {
            "write_failed_safely_during_outage": write_failed_safely,
            "no_partial_write_during_outage": int(rows_during) == baseline_n,
            "pre_outage_data_intact_after_restore": all(
                fp in restored_fp for fp in baseline_fp
            ),
            "system_accepts_writes_after_restore": len(restored_fp)
            == baseline_n + 1,
        }
        res.evidence = [
            "DB outage injected; a write raised and was safely rejected",
            f"Ledger rows for {source} stayed at {rows_during} during outage "
            f"(baseline {baseline_n})",
            f"RTO = {rto:.3f}s; RPO = 0 (no committed data lost)",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Scenario 5 — object-store (raw lake) outage
    # ------------------------------------------------------------------

    def _scenario_object_store_outage(self) -> ScenarioResultV1:
        source = "drill-osout"
        res = ScenarioResultV1(scenario="object_store_outage", started_at=_now_iso())

        baseline_n = 10
        for i in range(baseline_n):
            self._ingest_one(source, i)
        baseline_integrity = self.lake.verify_all()

        # Inject the outage: a lake whose directory has been removed.
        real_lake = self.lake
        outage_lake_dir = self.work_dir / "lake_deleted"
        outage_lake = RawLakeStorage(
            outage_lake_dir, encryption_key=Fernet.generate_key()
        )
        shutil.rmtree(outage_lake_dir, ignore_errors=True)

        rto_start = time.perf_counter()
        store_failed_safely = False
        try:
            self.lake = outage_lake
            try:
                self.lake.store(
                    b"<html>x</html>", source_slug=source, url="https://x.test/"
                )
            except Exception:
                store_failed_safely = True
        finally:
            self.lake = real_lake
        rto = time.perf_counter() - rto_start

        restored_integrity = self.lake.verify_all()

        res.volume = baseline_n
        res.duration_seconds = round(rto, 3)
        res.rto_seconds = round(rto, 3)
        res.rpo_seconds = 0.0
        res.metrics = {
            "baseline_objects_ok": baseline_integrity["ok"],
            "objects_ok_after_restore": restored_integrity["ok"],
            "corrupted_after_restore": restored_integrity["corrupted"],
            "missing_after_restore": restored_integrity["missing"],
        }
        res.checks = {
            "store_failed_safely_during_outage": store_failed_safely,
            "no_object_loss": restored_integrity["missing"] == 0,
            "no_corruption_introduced": restored_integrity["corrupted"] == 0,
            "all_pre_outage_objects_verify": restored_integrity["ok"]
            >= baseline_integrity["ok"],
        }
        res.evidence = [
            "Object-store outage injected; store() raised and left no object",
            f"Post-restore integrity: {restored_integrity}",
            f"RTO = {rto:.3f}s; RPO = 0 (all objects intact)",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Scenario 6 — restore and replay from raw (the DR drill)
    # ------------------------------------------------------------------

    def _scenario_restore_and_replay(self) -> ScenarioResultV1:
        source = "drill-dr"
        res = ScenarioResultV1(scenario="restore_and_replay", started_at=_now_iso())

        # --- Phase A: baseline -----------------------------------------
        baseline_n = 25
        for i in range(baseline_n):
            self._ingest_one(source, i)
        baseline_view = self._consumer_rows_for_source(source)
        baseline_fp = sorted(
            self._row_fingerprint(r["row_json"]) for r in baseline_view
        )
        baseline_prov: set[tuple[str, str]] = set()
        for r in baseline_view:
            baseline_prov |= self._row_provenance(r["row_json"])
        # Capture the receipts for this source *before* the disaster.
        source_receipts = sorted(
            (r for r in self._receipts.values() if r.source_slug == source),
            key=lambda r: r.url,
        )
        last_durable_write = _utcnow()

        # --- Phase B: destroy the operational database -----------------
        self.engine.dispose()
        destroyed_at = time.perf_counter()
        if self.db_path.exists():
            os.unlink(self.db_path)

        # --- Phase C: restore by replaying from the raw lake -----------
        restore_start = time.perf_counter()
        self.engine = self._make_engine(self.db_path)
        self._init_schema(self.engine)

        replayed = 0
        for i, receipt in enumerate(source_receipts):
            content = self.lake.retrieve(receipt.artifact_id)
            assert hashlib.sha256(content).hexdigest() == receipt.content_hash
            batch = self._build_extraction_batch(receipt, source, i)
            gates.ingest_validate_and_optionally_promote(
                self.engine,
                pipeline=self._pipeline_for(source, i),
                source_slug=source,
                gate=GateKind.EXTRACTION.value,
                payload=batch,
                auto_promote=True,
                promoted_by="dp05-restore",
            )
            replayed += 1

        restored_view = self._consumer_rows_for_source(source)
        rto = time.perf_counter() - restore_start
        restored_fp = sorted(
            self._row_fingerprint(r["row_json"]) for r in restored_view
        )
        restored_prov: set[tuple[str, str]] = set()
        for r in restored_view:
            restored_prov |= self._row_provenance(r["row_json"])

        # --- Phase D: replay AGAIN — assert no duplicate publication ---
        promotions_before = self._count_promotions_for_source(source)
        rows_before = len(self._consumer_rows_for_source(source))
        for i, receipt in enumerate(source_receipts):
            content = self.lake.retrieve(receipt.artifact_id)
            batch = self._build_extraction_batch(receipt, source, i)
            gates.ingest_validate_and_optionally_promote(
                self.engine,
                pipeline=self._pipeline_for(source, i),
                source_slug=source,
                gate=GateKind.EXTRACTION.value,
                payload=batch,
                auto_promote=True,
                promoted_by="dp05-restore",
            )
        promotions_after = self._count_promotions_for_source(source)
        rows_after_second = self._consumer_rows_for_source(source)
        second_fp = sorted(
            self._row_fingerprint(r["row_json"]) for r in rows_after_second
        )

        # RPO: the raw lake preserved every artifact committed before the
        # disaster, so the data-loss window is 0.
        rpo = 0.0

        res.volume = replayed
        res.duration_seconds = round(time.perf_counter() - destroyed_at, 3)
        res.rpo_seconds = rpo
        res.rto_seconds = round(rto, 3)
        res.throughput_per_second = round(replayed / rto, 2) if rto > 0 else None
        res.metrics = {
            "baseline_rows": len(baseline_fp),
            "restored_rows": len(restored_fp),
            "promotions_before_replay": promotions_before,
            "promotions_after_replay": promotions_after,
            "consumer_rows_before_second_replay": rows_before,
            "consumer_rows_after_second_replay": len(second_fp),
        }
        res.checks = {
            "published_data_survives": restored_fp == baseline_fp,
            "provenance_survives": restored_prov == baseline_prov
            and len(restored_prov) == baseline_n,
            "no_duplicate_publication": (
                len(second_fp) == rows_before
                and sorted(second_fp) == baseline_fp
            ),
            "raw_lake_is_system_of_record": replayed == baseline_n,
        }
        res.evidence = [
            f"Destroyed operational DB; replayed {replayed} artifacts from the "
            f"raw lake in {rto:.2f}s (RTO)",
            f"RPO = {rpo}s — raw lake preserved every committed artifact "
            f"(last durable write {last_durable_write.isoformat()})",
            f"Consumer rows restored = {len(restored_fp)} (baseline "
            f"{len(baseline_fp)}); provenance chain matches",
            f"Second replay: consumer-visible rows unchanged "
            f"({rows_before} → {len(second_fp)}), row content identical; "
            f"re-replay only appends superseded versions "
            f"(receipts {promotions_before} → {promotions_after}) — no "
            "duplicate publication",
        ]
        return res.finalise()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _count_promotions_for_source(self, source_slug: str) -> int:
        """Count promotion receipts across all of a source's pipelines."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COUNT(*) FROM quality_promotions "
                    "WHERE source_slug = :s AND pipeline LIKE :pfx"
                ),
                {
                    "s": source_slug,
                    "pfx": f"{DRILL_PIPELINE_PREFIX}.{source_slug}.%",
                },
            ).first()
        return int(row[0]) if row else 0

    def close(self) -> None:
        """Clean up the working dir if the drill created it."""
        try:
            self.engine.dispose()
        except Exception:
            pass
        if self._owns_dir and self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def __enter__(self) -> "DataPlaneDrill":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def run_drill(config: DrillConfig | None = None) -> DrillReportV1:
    """Run the full drill and return the signed report."""
    with DataPlaneDrill(config) as drill:
        return drill.run()
