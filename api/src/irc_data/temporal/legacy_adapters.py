"""OPS-02-04 — route every legacy CLI scraper through the OPS-01-02
:class:`~irc_data.temporal.ledger.workflows.SourceRunWorkflow`.

One scheduler, one run ledger, for *every* source in the register.

This module is the adapter layer between the OPS-01-02 workflow's
``run_registered_adapter`` activity and the existing DP-00 CLI scrapers
(``irc-data scrape …``).  It provides:

* :data:`LEGACY_CLI_ADAPTERS` — the register → adapter mapping.  One
  :class:`LegacyScraperAdapter` per legacy source slug:
  ``orc``, ``irc-tcc``, ``sailsys``, ``topyacht``, ``isora``, ``rhkyc``,
  ``sailracehq``, ``manage2sail``, ``irc-certs`` (certificate discovery/download **and**
  ``parse-certs`` ingestion), ``wayback-irc`` (plus a second wayback mode
  harvesting historical TCC listings), and ``sailing-news``.
* :func:`run_legacy_source` — the single dispatch entry point used by the
  ``run_registered_adapter`` activity.  Resolution order is:

  1. an explicit legacy adapter in :data:`LEGACY_CLI_ADAPTERS` (the OPS-02-04
     path — these run in-process so the run is heartbeat-able, and write both
     ledgers — see below);
  2. a DP-01 SDK adapter via the register row's ``adapter_class`` dotted
     path (``callable(record)``);
  3. ``None`` — the caller records a ledger-only run ("no adapter mapped").

* **Dual-write run accounting** (OPS-02-04 scope: *"every run writes
  ``source_runs`` (run ledger) and, until the admin reads ``source_runs``,
  ``ingestion_log`` too"*): :func:`open_source_run` /
  :func:`close_source_run` bridge into :func:`mirror_run_open_to_ingestion_log`
  / :func:`mirror_run_close_to_ingestion_log` so the OPS-01-03 admin keeps
  seeing rows on its existing ``ingestion_log`` view while ``source_runs``
  is the authoritative ledger.  Set ``SOURCE_RUNS_DUAL_WRITE=0`` to stop
  the ``ingestion_log`` mirror once the admin reads ``source_runs``
  directly.

Contract (per adapter)
----------------------

Every registered adapter satisfies the ``LegacyScraperAdapter`` contract —
verified by ``api/tests/temporal/test_legacy_adapters.py``:

* ``slug`` matches a ``data_sources.slug`` register row;
* ``argv()`` is the exact legacy CLI invocation (non-empty, starting with the
  ``irc-data`` sub-command);
* ``run(record)`` returns a JSON-able mapping that always carries
  ``records_written: int``;
* the CLI argv builder is pure (no network / DB side effects), so contract
  tests are hermetic.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "LEGACY_CLI_ADAPTERS",
    "LegacyScraperAdapter",
    "adapter_for_slug",
    "registered_slugs",
    "run_legacy_source",
    "mirror_run_open_to_ingestion_log",
    "mirror_run_close_to_ingestion_log",
    "DUAL_WRITE_ENV",
    "INGESTION_SOURCE_ALIASES",
]

#: Environment flag controlling the source_runs → ingestion_log dual-write.
#: "until the admin reads source_runs" — flip to "0"/"false" to disable.
DUAL_WRITE_ENV = "SOURCE_RUNS_DUAL_WRITE"

#: ingestion_log.source values used by the *legacy* CLI scrapers (and read by
#: the OPS-01-03 admin) for a given register slug.  The dual-write mirror
#: writes one ingestion_log row per alias so existing admin queries (which
#: filter on these historical source names) keep working unchanged.
INGESTION_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "irc-tcc": ("irc_tcc", "irc-tcc"),
    "wayback-irc": ("wayback", "wayback_tcc", "wayback-irc"),
}


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyScraperAdapter:
    """A legacy CLI scraper re-registered as an OPS-01-02 source adapter.

    Attributes
    ----------
    slug
        The governed ``data_sources.slug`` this adapter runs.
    cli_argv
        The legacy CLI invocation, exactly as cron used to run it (minus the
        leading ``irc-data`` executable), e.g. ``("scrape", "orc")``.
    description
        Human-readable note of what the run does.
    runner
        Optional in-process callable ``runner(record: Mapping) -> Mapping``.
        When set, :meth:`run` executes it directly (heartbeat-friendly); when
        ``None`` the CLI argv is executed in a subprocess.
    """

    slug: str
    cli_argv: tuple[str, ...]
    description: str = ""
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = field(
        default=None, compare=False, repr=False
    )

    # -- contract surface ----------------------------------------------------

    def argv(self, record: Mapping[str, Any] | None = None) -> list[str]:
        """Return the concrete CLI argv for a run (pure — no side effects)."""
        return list(self.cli_argv)

    def run(self, record: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Execute the legacy scraper; return a JSON-able stats mapping.

        Always includes ``records_written`` (int).  Failures raise — the
        SourceRunWorkflow records them in the ledger and marks the run failed.
        """
        started = time.monotonic()
        if self.runner is not None:
            out = self.runner(record or {})
        else:
            out = _run_cli_subprocess(self.argv(record))
        result = _normalise_result(out)
        result.setdefault("adapter", f"legacy-cli:{self.slug}")
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result


def _normalise_result(out: Any) -> dict[str, Any]:
    """Coerce loosely-typed legacy scraper returns into a JSON-able dict."""
    if isinstance(out, Mapping):
        result = dict(out)
        try:
            result["records_written"] = int(result.get("records_written") or 0)
        except (TypeError, ValueError):
            result["records_written"] = 0
        return result
    return {"records_written": 0, "adapter_output": str(out)[:500]}


def _run_cli_subprocess(argv: Sequence[str]) -> dict[str, Any]:
    """Execute a legacy ``irc-data`` CLI invocation as a subprocess."""
    import subprocess

    from irc_data.temporal.activities.scrape_activities import _irc_data_bin

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        [_irc_data_bin(), *argv],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"irc-data {' '.join(argv)} failed with code {proc.returncode}\n"
            f"STDOUT: {proc.stdout[-2000:]}\nSTDERR: {proc.stderr[-2000:]}"
        )
    return {"cli_stdout": proc.stdout[-1000:]}


# ---------------------------------------------------------------------------
# In-process runners (heartbeat-friendly; no subprocess shell-out)
# ---------------------------------------------------------------------------


def _run_orc(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Daily full ORC snapshot (legacy ``scrape orc``)."""
    import asyncio

    from irc_data.scrapers.orc import scrape_all_countries

    stats = asyncio.run(scrape_all_countries())
    return {
        "records_written": int(stats.get("total_new") or 0),
        "records_found": int(stats.get("total_found") or 0),
        "countries": stats.get("countries"),
        "snapshot_date": str(stats.get("snapshot_date")),
        "errors": len(stats.get("errors") or []),
    }


def _run_tcc(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """IRC TCC listings download + import (legacy ``scrape tcc``)."""
    import asyncio

    from irc_data.scrapers.tcc_listing import download_tcc_listing

    from datetime import date

    from sqlalchemy import text

    from irc_data.cli import cli
    from irc_data.db.connection import get_engine

    path = asyncio.run(download_tcc_listing())
    if not path:
        raise RuntimeError("TCC listing download failed (no CSV saved)")

    # Same gap as sailsys/topyacht before 42675f8: the legacy `scrape tcc`
    # imported after downloading, this adapter never did. Reuse the CLI's
    # import command rather than duplicating its two-pass import.
    today = date.today()
    count = text("SELECT count(*) FROM tcc_snapshots WHERE snapshot_date = :d")
    engine = get_engine()
    with engine.connect() as conn:
        before = conn.execute(count, {"d": today}).scalar()
    cli.main(args=["import", str(path), "--snapshot-date", str(today)], standalone_mode=False)
    with engine.connect() as conn:
        after = conn.execute(count, {"d": today}).scalar()
    return {"records_written": after - before, "snapshot_rows_today": after, "downloaded": str(path)}


def _last_successful_run_since(slug: str, lookback_days: int = 1) -> "date | None":
    """Date to pass as ``since=``: the last successful run's finish, minus one day.

    Returns None (full scrape) when there's no prior success — including
    the very first run, which is expected to be a genuine one-off backfill.

    Scheduled runs all share one ``source_runs`` row (run_key "scheduled"),
    which is already back to "running" when this is called, so querying it
    for status='success' fell through to a September ad-hoc row and every
    30-minute sailsys run re-scraped five weeks. The ledger mirrors each
    close into ``ingestion_log`` (metadata.trigger = "mirror-close"), which
    keeps one row per run. The one-day margin matches the old CLI's
    incremental cutoff and covers results posted late on the same day.
    """
    from datetime import timedelta

    from sqlalchemy import text

    from irc_data.db.connection import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT max(t) FROM ("
                "  SELECT completed_at AS t FROM ingestion_log"
                "  WHERE source = :slug AND status = 'completed'"
                "  AND metadata->>'trigger' = 'mirror-close'"
                "  UNION ALL"
                "  SELECT finished_at FROM source_runs"
                "  WHERE source_slug = :slug AND status = 'success'"
                ") s"
            ),
            {"slug": slug},
        ).first()
    return (row[0] - timedelta(days=lookback_days)).date() if row and row[0] else None


def drop_reused_race_copies(results, existing_dates: dict) -> tuple[list, int]:
    """Drop results whose SailSys race_id is already stored under a different date.

    A race_id is one sailed race; seeing it again under a new date means the
    club reused last season's series and SailSys re-dated the old results
    (SHCC, Oct 2026: ~159 copied rows with identical boats and places).
    """
    kept, dropped = [], 0
    for r in results:
        rid = str((r.raw_data or {}).get("race_id") or "")
        dates = existing_dates.get(rid)
        if rid and dates and r.event_date not in dates:
            dropped += 1
            continue
        kept.append(r)
    return kept, dropped


def _existing_sailsys_race_dates(engine, race_ids) -> dict:
    from sqlalchemy import text

    ids = sorted({str(i) for i in race_ids if i})
    if not ids:
        return {}
    out: dict = {}
    with engine.connect() as conn:
        for rid, d in conn.execute(
            text("SELECT DISTINCT raw_data->>'race_id', event_date FROM race_results"
                 " WHERE source = 'sailsys' AND raw_data->>'race_id' = ANY(:ids)"),
            {"ids": ids},
        ):
            out.setdefault(rid, set()).add(d)
    return out


def _run_sailsys(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """SailSys results, all clubs (legacy ``scrape results --source sailsys --all-clubs``)."""
    import asyncio

    from irc_data.db.connection import get_engine
    from irc_data.scrapers.result_import import import_scraper_results
    from irc_data.scrapers.sailsys import CLUBS, scrape_club_irc_results

    # Every run before this fix did a full, unbounded, all-history scrape
    # of every club — fine as a one-off backfill, not for a 30-minute
    # cadence source (observed: over an hour and still going on the first
    # real run under working infrastructure). Only the very first run per
    # club now does the full pull; every run after uses `since` from the
    # last success, matching what the underlying scraper already supports
    # (it was just never wired up — see scrape_club_irc_results's own
    # `since` parameter docs: "Only scrape races after this date
    # (incremental mode)").
    # 14-day lookback: results are often posted (or corrected) days after the
    # race, and the filter is on race date. A 1-day window silently skipped
    # anything posted late (RSYS Ladies of the Sea regatta, 3-4 Oct 2026).
    since = _last_successful_run_since("sailsys", lookback_days=14)
    engine = get_engine()
    copies_dropped = 0
    total = 0
    written = 0
    per_club: dict[str, int] = {}
    for club_name, club_id in CLUBS.items():
        results = asyncio.run(scrape_club_irc_results(club_id=club_id, since=since))
        per_club[club_name] = len(results or [])
        total += per_club[club_name]
        # This call was simply missing (9 Oct 2026) — "records_written": 0
        # was hardcoded with a comment claiming the import "happens
        # downstream", which was never true for this code path: nothing
        # ever called it. The scraper itself was healthy and finding real
        # new results every 30 minutes for over a month (confirmed:
        # records_found=239 on a single October run, RANSA/SHCC among
        # others) while every one of them was silently discarded. Matches
        # the CLI's own `scrape results --source sailsys --store` call
        # exactly (irc_data/cli.py).
        if results:
            existing = _existing_sailsys_race_dates(engine, ((r.raw_data or {}).get("race_id") for r in results))
            results, dropped = drop_reused_race_copies(results, existing)
            copies_dropped += dropped
        if results:
            stats = import_scraper_results(engine, results, source="sailsys", organizing_club=club_name)
            written += stats.get("imported", 0)
    return {
        "records_written": written,
        "records_found": total,
        "clubs": per_club,
        "since": str(since) if since else None,
        "reused_race_copies_dropped": copies_dropped,
    }


def _run_topyacht(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """TopYacht incremental (legacy ``scrape results --source topyacht --incremental --store``)."""
    import asyncio

    from irc_data.db.connection import get_engine
    from irc_data.scrapers.result_import import import_scraper_results
    from irc_data.scrapers.topyacht import scrape_all_clubs

    # Two real bugs fixed here together (9 Oct 2026), found diagnosing the
    # scrape-watchdog alert: (1) despite the "incremental" name/docstring,
    # `since` was never actually passed to scrape_all_clubs — every daily
    # run did a full, unbounded re-scrape of every club's entire history.
    # (2) "records_written": 0 was hardcoded with no persistence call at
    # all, identical to the sailsys bug fixed alongside this — confirmed
    # via source_runs: this source had reported "success" every single day
    # for 5+ months while writing zero new race_results rows the entire
    # time, because nothing here ever called import_scraper_results.
    from irc_data.scrapers.topyacht import scrape_events_directory

    # Same late-posting problem as sailsys: race-date filter, so look back 14 days.
    since = _last_successful_run_since("topyacht", lookback_days=14)
    engine = get_engine()
    results = asyncio.run(scrape_all_clubs(since=since)) or []
    # Most clubs now publish at paths the fixed club list can't guess (or on
    # their own sites); TopYacht's events directory links each event's real
    # results page (2026-10-11).
    directory_results = asyncio.run(scrape_events_directory(since=since)) or []
    results = results + directory_results
    written = 0
    if results:
        stats = import_scraper_results(engine, results, source="topyacht")
        written = stats.get("imported", 0)
    return {
        "records_written": written,
        "records_found": len(results),
        "records_found_directory": len(directory_results),
        "since": str(since) if since else None,
    }


def _record_since(record: Mapping[str, Any]) -> "date | None":
    """Operator override: ``record["since"]`` as an ISO date (backfills)."""
    from datetime import date

    value = record.get("since") if record else None
    return date.fromisoformat(str(value)) if value else None


def _run_event_source(
    record: Mapping[str, Any],
    *,
    slug: str,
    source: str,
    make_source: Callable[[], Any],
    include_class_name: bool = False,
) -> dict[str, Any]:
    """Discover → scrape → persist for a DP-01 ``RaceResultSource``.

    Until Nov 2026 the isora/rhkyc/sailracehq/rorc/sydney-hobart/cowesweek/
    yachtscoring runners scraped every event and then returned
    ``records_written: 0`` without persisting anything (the same bug fixed
    for sailsys/topyacht in Oct 2026): every scheduled "success" threw its
    results away. Results now go through
    :func:`~irc_data.scrapers.result_import.store_normalized_results`, the
    exact per-row loop the CLI's ``scrape results --source <x> --store``
    uses, under the same ``race_results.source`` value (``source`` — note
    the register slug ``sydney-hobart`` stores as ``sydneyhobart``).

    * ``since`` is ``record["since"]`` if given, else the last successful
      run minus a 14-day lookback (results are filtered by race/event date
      and are often posted late). Most of these scrapers filter by season
      year, so a run re-reads the current season; re-runs are idempotent
      because ``upsert_race_result`` returns the existing row for an
      already-stored entry+race.
    * Each event is persisted as soon as it is scraped, so a run that dies
      part-way keeps what it already fetched; one failing event is recorded
      and skipped rather than discarding the run (the run fails only if
      every event failed).
    * ``records_written`` is the real number of new rows (row-count delta),
      not the number of upsert calls.
    """
    import asyncio

    from irc_data.db.connection import get_engine
    from irc_data.db.operations import log_ingestion_end, log_ingestion_start
    from irc_data.scrapers.result_import import store_normalized_results

    since = _record_since(record) or _last_successful_run_since(slug, lookback_days=14)
    src = make_source()
    events = asyncio.run(src.discover_events(since=since)) or []

    engine = get_engine()
    log_id = log_ingestion_start(engine, source, metadata={"adapter": f"legacy-cli:{slug}", "since": str(since) if since else None})
    found = written = matched = store_errors = events_with_results = 0
    newest = None
    event_errors: list[str] = []
    store_error_details: list[str] = []
    try:
        for event in events:
            try:
                rows = asyncio.run(src.scrape_event(event)) or []
            except Exception as e:  # one broken event must not discard the run
                event_errors.append(f"{event.event_name}: {type(e).__name__}: {e}"[:300])
                continue
            if not rows:
                continue
            events_with_results += 1
            found += len(rows)
            dates = [r.event_date for r in rows if r.event_date]
            if dates:
                newest = max([newest, *dates]) if newest else max(dates)
            stats = store_normalized_results(
                engine, rows, source=source, include_class_name=include_class_name, log=False,
            )
            written += stats["written"]
            matched += stats["matched"]
            store_errors += stats["errors"]
            store_error_details.extend(stats["error_details"][: max(0, 20 - len(store_error_details))])
        if events and len(event_errors) == len(events):
            raise RuntimeError(
                f"{slug}: all {len(events)} events failed to scrape; first: {event_errors[0]}"
            )
    except Exception as e:
        log_ingestion_end(engine, log_id, status="failed", records_found=found,
                          records_new=written, error_message=str(e)[:1000])
        raise
    errors = event_errors + store_error_details
    log_ingestion_end(
        engine, log_id,
        status="completed" if not errors else "completed_with_errors",
        records_found=found,
        records_new=written,
        error_message="; ".join(errors)[:1000] if errors else None,
    )
    return {
        "records_written": written,
        "records_found": found,
        "events": len(events),
        "events_with_results": events_with_results,
        "event_errors": len(event_errors),
        "store_errors": store_errors,
        "boats_matched_rows": matched,
        "newest_event_date": str(newest) if newest else None,
        "since": str(since) if since else None,
        "source": source,
    }


def _run_isora(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """ISORA results (legacy ``scrape results --source isora --store``)."""
    from irc_data.scrapers.isora import ISORASource

    return _run_event_source(record, slug="isora", source="isora",
                             make_source=ISORASource, include_class_name=True)


def _run_rhkyc(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """RHKYC results (legacy ``scrape results --source rhkyc --store``)."""
    from irc_data.scrapers.legacy.rhkyc import RHKYCSource

    return _run_event_source(record, slug="rhkyc", source="rhkyc",
                             make_source=RHKYCSource, include_class_name=True)


def _run_sailracehq(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """SailRaceHQ / RORC 2023+ results (legacy ``scrape results --source sailracehq --store``)."""
    from irc_data.scrapers.sailracehq import SailRaceHQSource

    return _run_event_source(record, slug="sailracehq", source="sailracehq",
                             make_source=SailRaceHQSource)


def _run_rorc(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """RORC static-HTML results archive, 2007-2022 (legacy ``scrape results --source rorc --store``).

    RORC moved to SailRaceHQ in 2023 (the ``sailracehq`` source), so with an
    incremental ``since`` in 2023+ this correctly finds nothing; it only
    returns rows for an explicit archive backfill (``record["since"]``).
    """
    from irc_data.scrapers.rorc import RORCSource

    return _run_event_source(record, slug="rorc", source="rorc", make_source=RORCSource)


def _run_sydneyhobart(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sydney Hobart results (legacy ``scrape results --source sydneyhobart --store``).

    Register slug ``sydney-hobart``; rows are stored as ``source='sydneyhobart'``
    like every existing row and the CLI.
    """
    from irc_data.scrapers.sydneyhobart import SydneyHobartSource

    return _run_event_source(record, slug="sydney-hobart", source="sydneyhobart",
                             make_source=SydneyHobartSource)


def _run_cowesweek(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Cowes Week results (legacy ``scrape results --source cowesweek --store``)."""
    from irc_data.scrapers.cowesweek import CowesWeekSource

    return _run_event_source(record, slug="cowesweek", source="cowesweek",
                             make_source=CowesWeekSource, include_class_name=True)


def _run_yachtscoring(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """YachtScoring results (no CLI ``--store`` path ever existed).

    ``YachtScoringSource`` only scrapes event ids it is given, so a run
    without ``record["event_ids"]`` discovers nothing.
    """
    from irc_data.scrapers.yachtscoring import YachtScoringSource

    event_ids = [int(i) for i in (record.get("event_ids") or [])]
    out = _run_event_source(record, slug="yachtscoring", source="yachtscoring",
                            make_source=lambda: YachtScoringSource(event_ids=event_ids),
                            include_class_name=True)
    if not event_ids:
        out["warning"] = "no event_ids given and YachtScoringSource has no event discovery"
    return out


def _run_manage2sail(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Manage2Sail IRC results: discover, scrape and persist.

    The register row's ``adapter_class`` (``...manage2sail.Manage2SailScraper``)
    is a scraper class, not a ``callable(record) -> stats``, so this explicit
    runner is what the workflow executes. Persists through
    ``manage2sail.store_results`` (upsert_race_result) and reports the real
    number of new race_results rows. Same 14-day late-posting lookback as
    sailsys/topyacht.
    """
    from datetime import date

    from irc_data.scrapers import manage2sail

    since = None
    if record.get("since"):
        since = date.fromisoformat(str(record["since"]))
    else:
        since = _last_successful_run_since("manage2sail", lookback_days=14)
    return manage2sail.run(dict(record), since=since)


def _run_cert_discovery(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """IRC certificate discovery — exhaustive 2-letter search + download.

    Legacy: ``scrape certs --exhaustive``.
    """
    import asyncio

    from irc_data.scrapers.certificate_bulk import (
        download_certificates,
        exhaustive_enumerate,
    )

    certs = asyncio.run(exhaustive_enumerate())
    downloaded = asyncio.run(download_certificates(certs))
    return {
        "records_written": len(downloaded or []),
        "records_found": len(certs or []),
    }


def _run_cert_parse(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """IRC certificate parse — parse downloaded PDFs into the database.

    Legacy: ``parse-certs``.
    """
    from sqlalchemy import text

    from irc_data.cli import cli
    from irc_data.config import CERTIFICATES_DIR
    from irc_data.db.connection import get_engine

    # Same throw-away bug as the results runners: this parsed every PDF and
    # discarded the parse (records_written hardcoded 0). Run the CLI's own
    # parse-certs (which upserts each certificate on cert_number) and report
    # the real number of new certificate rows.
    engine = get_engine()
    count = text("SELECT count(*) FROM certificates")
    with engine.connect() as conn:
        before = int(conn.execute(count).scalar() or 0)
    cli.main(args=["parse-certs"], standalone_mode=False)
    with engine.connect() as conn:
        after = int(conn.execute(count).scalar() or 0)
    pdfs = len(list(CERTIFICATES_DIR.glob("*.pdf"))) if CERTIFICATES_DIR.exists() else 0
    return {"records_written": after - before, "records_found": pdfs}


def _run_wayback_certs(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Wayback Machine harvest of historical IRC certificate PDFs.

    Legacy: ``scrape wayback``.
    """
    import asyncio

    from irc_data.scrapers.wayback import find_and_download_all

    downloaded = asyncio.run(find_and_download_all())
    return {"records_written": len(downloaded or [])}


def _run_wayback_tcc(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Wayback Machine harvest of historical TCC listings.

    Legacy: ``wayback-tcc``.
    """
    import asyncio

    from irc_data.scrapers.wayback import harvest_tcc_archives

    archives = asyncio.run(harvest_tcc_archives())
    return {"records_written": len(archives or [])}


def _run_boat_news(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Boat news via Firecrawl + Claude (legacy ``scrape-news``)."""
    from irc_data.temporal.activities.scrape_activities import run_cli_command

    out = run_cli_command(["scrape-news"])
    return {"records_written": 0, "cli_stdout": out[-1000:]}


# ---------------------------------------------------------------------------
# The registry — one adapter per legacy source
# ---------------------------------------------------------------------------

#: Register slug → legacy adapter.  This is the OPS-02-04 routing table: the
#: SourceRunWorkflow's ``run_registered_adapter`` activity consults it first
#: (before falling back to ``adapter_class`` DP-01 SDK adapters).
#:
#: Slugs follow the ``data_sources`` register exactly.  Certificate
#: discovery/download and certificate parse are two halves of the legacy
#: ``irc-certs`` source — the default run chains discovery → parse, matching
#: the old cron pairing of ``scrape certs`` + ``parse-certs``.
def _chain_cert_discovery_and_parse(record: Mapping[str, Any]) -> Mapping[str, Any]:
    discovery = _run_cert_discovery(record)
    parsed = _run_cert_parse(record)
    return {
        "records_written": int(discovery.get("records_written") or 0),
        "records_found": int(parsed.get("records_found") or 0),
        "discovery": dict(discovery),
        "parse": dict(parsed),
    }


LEGACY_CLI_ADAPTERS: dict[str, LegacyScraperAdapter] = {
    "orc": LegacyScraperAdapter(
        slug="orc",
        cli_argv=("scrape", "orc"),
        description="Daily full ORC snapshot from data.orc.org.",
        runner=_run_orc,
    ),
    "irc-tcc": LegacyScraperAdapter(
        slug="irc-tcc",
        cli_argv=("scrape", "tcc"),
        description="IRC TCC listings CSV download + tcc_snapshots import.",
        runner=_run_tcc,
    ),
    "sailsys": LegacyScraperAdapter(
        slug="sailsys",
        cli_argv=("scrape", "results", "--source", "sailsys", "--all-clubs"),
        description="SailSys race results across all clubs.",
        runner=_run_sailsys,
    ),
    "topyacht": LegacyScraperAdapter(
        slug="topyacht",
        cli_argv=("scrape", "results", "--source", "topyacht", "--incremental", "--store"),
        description="TopYacht incremental results scrape.",
        runner=_run_topyacht,
    ),
    "isora": LegacyScraperAdapter(
        slug="isora",
        cli_argv=("scrape", "results", "--source", "isora"),
        description="ISORA race results.",
        runner=_run_isora,
    ),
    "rhkyc": LegacyScraperAdapter(
        slug="rhkyc",
        cli_argv=("scrape", "results", "--source", "rhkyc"),
        description="RHKYC race results.",
        runner=_run_rhkyc,
    ),
    "sailracehq": LegacyScraperAdapter(
        slug="sailracehq",
        cli_argv=("scrape", "results", "--source", "sailracehq"),
        description="SailRaceHQ race results.",
        runner=_run_sailracehq,
    ),
    "rorc": LegacyScraperAdapter(
        slug="rorc",
        cli_argv=("scrape", "results", "--source", "rorc"),
        description="RORC race results.",
        runner=_run_rorc,
    ),
    "sydney-hobart": LegacyScraperAdapter(
        slug="sydney-hobart",
        cli_argv=("scrape", "results", "--source", "sydneyhobart"),
        description="Sydney Hobart race results.",
        runner=_run_sydneyhobart,
    ),
    "cowesweek": LegacyScraperAdapter(
        slug="cowesweek",
        cli_argv=("scrape", "results", "--source", "cowesweek"),
        description="Cowes Week race results.",
        runner=_run_cowesweek,
    ),
    "yachtscoring": LegacyScraperAdapter(
        slug="yachtscoring",
        cli_argv=("scrape", "results", "--source", "yachtscoring"),
        description="YachtScoring race results.",
        runner=_run_yachtscoring,
    ),
    "manage2sail": LegacyScraperAdapter(
        slug="manage2sail",
        cli_argv=("scrape", "results", "--source", "manage2sail"),
        description="Manage2Sail IRC race results (event list + known IRC clubs).",
        runner=_run_manage2sail,
    ),
    "irc-certs": LegacyScraperAdapter(
        slug="irc-certs",
        cli_argv=("scrape", "certs", "--exhaustive"),
        description=(
            "IRC certificate discovery (exhaustive 2-letter search + PDF "
            "download) followed by parse-certs ingestion."
        ),
        runner=_chain_cert_discovery_and_parse,
    ),
    "wayback-irc": LegacyScraperAdapter(
        slug="wayback-irc",
        cli_argv=("scrape", "wayback"),
        description=(
            "Wayback Machine harvest — historical IRC certificate PDFs "
            "(scrape wayback) and historical TCC listings (wayback-tcc)."
        ),
        runner=_run_wayback_certs,
    ),
    "sailing-news": LegacyScraperAdapter(
        slug="sailing-news",
        cli_argv=("scrape-news",),
        description="Boat news via Firecrawl + Claude.",
        runner=_run_boat_news,
    ),
}

#: Alternate wayback mode exposed for operators/tests — harvest historical
#: TCC listings instead of certificate PDFs.  Not a separate register row;
#: selected via ``record["mode"] == "tcc"`` in :func:`run_legacy_source`.
_WAYBACK_TCC_ADAPTER = LegacyScraperAdapter(
    slug="wayback-irc",
    cli_argv=("wayback-tcc",),
    description="Wayback Machine harvest of historical IRC TCC listings.",
    runner=_run_wayback_tcc,
)


def registered_slugs() -> list[str]:
    """All register slugs with a legacy CLI adapter (sorted)."""
    return sorted(LEGACY_CLI_ADAPTERS)


def adapter_for_slug(slug: str, *, mode: str | None = None) -> LegacyScraperAdapter | None:
    """Return the legacy adapter for *slug* (or ``None`` when unmapped)."""
    if slug == "wayback-irc" and mode == "tcc":
        return _WAYBACK_TCC_ADAPTER
    return LEGACY_CLI_ADAPTERS.get(slug)


# ---------------------------------------------------------------------------
# Dispatch entry point (called by the run_registered_adapter activity)
# ---------------------------------------------------------------------------


def _load_dotted(path: str) -> Callable[..., Any]:
    import importlib

    module_name, _, attr = path.rpartition(".")
    if not module_name:
        raise ImportError(f"invalid dotted path: {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


async def run_legacy_source(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve and execute the adapter for a register record.

    *record* is the dict produced by ``fetch_source_record`` (carries
    ``slug``, ``base_url``, ``cadence``, ``adapter_class``, …).  Returns
    ``None`` when no adapter is mapped for the slug (the caller records a
    ledger-only run).
    """
    import asyncio
    import inspect

    slug = str(record.get("slug") or "")
    mode = record.get("mode")

    adapter = adapter_for_slug(slug, mode=mode if isinstance(mode, str) else None)
    if adapter is not None:
        # adapter.run() is sync, but most legacy runners (_run_sailsys,
        # _run_topyacht, _run_isora, _run_rhkyc, _run_sailracehq, …) call
        # asyncio.run() internally to drive an async scraper. Called
        # directly here, that always raised "asyncio.run() cannot be
        # called from a running event loop" — this coroutine already runs
        # inside Temporal's activity event loop. Every source using this
        # pattern failed every run, invisibly, until this was noticed
        # (nothing had actually executed a scheduled run to surface it —
        # see the schedule task-queue fix in registry.py). A thread has no
        # event loop of its own, so the nested asyncio.run() inside the
        # runner works there.
        return await asyncio.get_event_loop().run_in_executor(
            None, adapter.run, record
        )

    adapter_class = record.get("adapter_class")
    if adapter_class:
        fn = _load_dotted(str(adapter_class))
        out = fn(record)
        if inspect.isawaitable(out):
            out = await out
        return _normalise_result(out)

    return None


# ---------------------------------------------------------------------------
# Dual-write bridge: source_runs → ingestion_log
# ---------------------------------------------------------------------------


def _dual_write_enabled() -> bool:
    return os.environ.get(DUAL_WRITE_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _ingestion_sources_for(slug: str) -> tuple[str, ...]:
    """ingestion_log.source aliases to mirror for a register slug."""
    return INGESTION_SOURCE_ALIASES.get(slug, (slug,))


def mirror_run_open_to_ingestion_log(
    engine: Any,
    source_slug: str,
    *,
    run_key: str,
    trigger: str = "schedule",
    workflow_id: str | None = None,
    started_at: Any = None,
) -> dict[str, int]:
    """Open one ``ingestion_log`` row per legacy source alias for this run.

    Returns ``{ingestion_source: log_id}`` so the close path can update the
    exact rows.  No-op (returns ``{}``) when dual-write is disabled.
    """
    if not _dual_write_enabled():
        return {}
    from irc_data.db.run_ledger import record_run_start

    metadata = {
        "run_key": run_key,
        "trigger": trigger,
        "workflow_id": workflow_id,
        "source_slug": source_slug,
        "ledger": "source_runs",  # provenance: source_runs is authoritative
    }
    ids: dict[str, int] = {}
    for source in _ingestion_sources_for(source_slug):
        ids[source] = record_run_start(
            engine, source, metadata=metadata, started_at=started_at
        )
    return ids


def mirror_run_close_to_ingestion_log(
    engine: Any,
    source_slug: str,
    *,
    run_key: str,
    status: str,
    detail: str | None = None,
    stats: Mapping[str, Any] | None = None,
    log_ids: Mapping[str, int] | None = None,
) -> None:
    """Close the mirrored ``ingestion_log`` rows for this run.

    ``status`` is the source_runs vocabulary (``success``/``failed``); it is
    mapped onto the ingestion_log vocabulary (``completed``/``failed``).
    Row counts come from the adapter ``stats`` (``records_found`` /
    ``records_new`` / ``records_written``).  When *log_ids* is not supplied
    (e.g. the workflow closed after a replay) the open rows are looked up by
    ``run_key`` in the metadata.  No-op when dual-write is disabled.
    """
    if not _dual_write_enabled():
        return
    from sqlalchemy import text

    from irc_data.db.run_ledger import (
        STATUS_COMPLETED,
        STATUS_FAILED,
        record_run_end,
    )

    stats = stats or {}
    ledger_status = STATUS_COMPLETED if status == "success" else (
        status if status in (STATUS_COMPLETED, STATUS_FAILED, "running") else STATUS_FAILED
    )
    records_found = _as_int(stats.get("records_found"))
    records_new = _as_int(
        stats.get("records_new", stats.get("records_written"))
    )
    records_updated = _as_int(stats.get("records_updated"))

    resolved: dict[str, int] = dict(log_ids or {})
    if not resolved:
        # Find open mirrored rows by run_key (post-replay / cross-process path).
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id, source FROM ingestion_log "
                        "WHERE status = 'running' "
                        "AND metadata->>'run_key' = :run_key"
                    ),
                    {"run_key": run_key},
                ).all()
            resolved = {str(source): int(rid) for rid, source in rows}
        except Exception:
            resolved = {}

    if not resolved:
        # Nothing to close (dual-write may have been off at open time) —
        # leave a completed marker row so the run is still observable.
        resolved = {
            s: rid
            for s, rid in mirror_run_open_to_ingestion_log(
                engine, source_slug, run_key=run_key, trigger="mirror-close"
            ).items()
        }

    for source, log_id in resolved.items():
        record_run_end(
            engine,
            int(log_id),
            status=ledger_status,
            records_found=records_found,
            records_new=records_new,
            records_updated=records_updated,
            error_message=(detail[:1000] if detail and ledger_status == STATUS_FAILED else None),
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
