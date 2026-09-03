"""Firecrawl cutover machinery (OPS-02-06).

Drives the "run legacy + Firecrawl in parallel, then cut over when the
parity gate passes" workflow for the long-tail results sources (ISORA,
SailRaceHQ, Cowes Week).

What "cut over" means concretely, per the task contract:

- **pause legacy** — the bespoke legacy scraper is stopped from producing
  new rows. We record this on the source's ``data_sources`` row by marking
  the previous legacy ``adapter_class`` as paused (moved into ``notes``) and
  stamping an audit note; the nightly cron for the legacy adapter is the
  thing an operator comments out (see the runbook), but the *register* is
  the source of truth for "which transport is live".
- **update ``data_sources.adapter_class``** — repoint the register at the
  Firecrawl discovery pipeline adapter so downstream tooling (and the
  Temporal ledger's ``_load_dotted(adapter_class)``) resolves the source to
  the Firecrawl path.
- **transport='firecrawl'** — after cutover, rows for the source arrive via
  ``irc-data discover-and-ingest --source X`` (which writes
  ``transport='firecrawl'``); the gate confirms this.

The gate that authorises a cutover is :mod:`irc_data.diagnostics.parity_gate`
(14-day window, row capture ≥ 95%, place-1 agreement ≥ 98%). The cutover CLI
*refuses* to cut over a source whose gate does not pass unless the operator
passes ``--force`` (an explicit, audited override).

Cutovers are recorded in ``ingest_events`` (status='cutover') so the trail
is durable and visible to the data-health tooling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import click
from sqlalchemy import text

from irc_data.db.connection import get_engine

logger = logging.getLogger(__name__)

#: Sources eligible for the OPS-02-06 Firecrawl cutover.
CUTOVER_SOURCES: tuple[str, ...] = ("isora", "sailracehq", "cowesweek", "rhkyc")

#: The adapter the register is repointed to after cutover. The Temporal
#: ledger resolves ``adapter_class`` via ``_load_dotted``; this dotted path
#: maps the source onto the Firecrawl discovery-and-ingest pipeline.
FIRECRAWL_ADAPTER = "irc_data.discovery.orchestrator.seed_crawl_and_ingest"


@dataclass
class CutoverState:
    slug: str
    adapter_class: str | None
    adapter_status: str
    enabled: bool
    is_firecrawl: bool
    legacy_paused: bool
    transport_last_14d: dict[str, int]
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _transport_counts(engine, slug: str, days: int = 14) -> dict[str, int]:
    # Dialect-portable date cutoff (works on Postgres + SQLite).
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT transport, COUNT(*) AS n
            FROM race_results
            WHERE source = :slug
              AND created_at >= :cutoff
            GROUP BY transport
        """), {"slug": slug, "cutoff": cutoff}).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        key = r[0] if r[0] is not None else "untagged"
        out[str(key)] = int(r[1])
    return out


def get_cutover_state(engine, slug: str) -> CutoverState:
    """Read the current cutover state for one source from the register."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT slug, adapter_class, adapter_status, enabled, notes
            FROM data_sources WHERE slug = :slug
        """), {"slug": slug}).first()
    if row is None:
        raise click.BadParameter(f"no data_sources row for slug={slug!r}")

    is_fc = bool(row.adapter_class and "firecrawl" in row.adapter_class.lower()
                 or (row.adapter_class or "") == FIRECRAWL_ADAPTER)
    # Legacy is considered paused when the adapter no longer points at a
    # bespoke scraper *or* the source is disabled.
    legacy_paused = is_fc or (not row.enabled) or (
        row.adapter_status in ("deprecated", "paused")
    )
    return CutoverState(
        slug=row.slug,
        adapter_class=row.adapter_class,
        adapter_status=row.adapter_status,
        enabled=row.enabled,
        is_firecrawl=is_fc,
        legacy_paused=legacy_paused,
        transport_last_14d=_transport_counts(engine, row.slug),
        notes=row.notes,
    )


def cutover(
    engine,
    slug: str,
    *,
    days: int = 14,
    force: bool = False,
    dry_run: bool = False,
    firecrawl_adapter: str = FIRECRAWL_ADAPTER,
) -> dict[str, Any]:
    """Cut a source over to Firecrawl if (and only if) the parity gate passes.

    Returns a result dict describing what happened (or would happen, when
    ``dry_run``). Raises no exception on gate failure — the result dict
    carries ``cut_over=False`` and the gate reason.
    """
    from irc_data.diagnostics.parity_gate import evaluate_parity_gate

    if slug not in CUTOVER_SOURCES:
        raise click.BadParameter(
            f"{slug!r} is not an OPS-02-06 cutover source {CUTOVER_SOURCES}"
        )

    gate = evaluate_parity_gate(engine, slug, days=days)
    state = get_cutover_state(engine, slug)

    result: dict[str, Any] = {
        "slug": slug,
        "gate": gate.as_dict(),
        "before": state.as_dict(),
        "cut_over": False,
        "forced": force,
        "dry_run": dry_run,
        "actions": [],
    }

    if state.is_firecrawl and state.legacy_paused:
        result["actions"].append("already cut over (adapter is Firecrawl, legacy paused)")
        result["cut_over"] = True
        return result

    if not gate.passed and not force:
        result["actions"].append(f"REFUSED: parity gate not passed ({gate.reason})")
        return result
    if force and not gate.passed:
        result["actions"].append(
            f"WARNING: forcing cutover despite gate failure ({gate.reason})"
        )

    now = datetime.now(timezone.utc).isoformat()
    legacy_adapter = state.adapter_class or ""
    note = (
        f"OPS-02-06 cutover {now}: legacy adapter "
        f"'{legacy_adapter}' paused; adapter repointed to Firecrawl "
        f"discovery pipeline. Gate: {gate.reason}"
    )
    # SQLite stores booleans as 1/0; Postgres uses true/false. Use a
    # dialect-appropriate literal.
    enabled_lit = "true" if engine.dialect.name == "postgresql" else "1"

    if dry_run:
        result["actions"].append(
            f"DRY-RUN would set adapter_class={firecrawl_adapter}, "
            f"adapter_status='active', append note, and record an "
            f"ingest_events cutover row"
        )
        result["cut_over"] = True
        return result

    # --- Apply the cutover ---------------------------------------------
    with engine.begin() as conn:
        conn.execute(text(f"""
            UPDATE data_sources
            SET adapter_class = :adapter,
                adapter_status = 'active',
                enabled = {enabled_lit},
                notes = COALESCE(notes, '') || :sep || :note,
                updated_at = CURRENT_TIMESTAMP
            WHERE slug = :slug
        """), {"adapter": firecrawl_adapter, "note": note,
               "sep": "\n\n", "slug": slug})

        # Durable audit trail (surfaced by the data-health tooling).
        conn.execute(text("""
            INSERT INTO ingest_events
              (source, event_type, reference, status, reason, meta, created_at)
            VALUES
              (:source, 'cutover', :ref, 'ok', :reason, :meta, CURRENT_TIMESTAMP)
        """), {
            "source": slug,
            "ref": f"cutover://{slug}",
            "reason": note,
            "meta": json.dumps({
                "gate": gate.as_dict(),
                "previous_adapter": legacy_adapter,
                "new_adapter": firecrawl_adapter,
                "forced": force,
            }),
        })

    result["actions"].append(
        f"adapter_class -> {firecrawl_adapter}; legacy '{legacy_adapter}' "
        f"paused; adapter_status='active'; ingest_events cutover row written"
    )
    result["cut_over"] = True
    result["after"] = get_cutover_state(engine, slug).as_dict()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command(name="cutover-status")
@click.option("--source", "sources", multiple=True,
              help="Limit to specific source slug(s). Default: all cutover sources.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def cutover_status(sources, as_json):
    """Show the cutover state of the OPS-02-06 sources.

    Reports, per source: the registered adapter, whether it is the Firecrawl
    pipeline, whether the legacy scraper is paused, and the 14-day transport
    row split (the "rows arrive with transport='firecrawl'" evidence).
    """
    engine = get_engine()
    slugs = list(sources) if sources else list(CUTOVER_SOURCES)
    states = []
    for s in slugs:
        try:
            states.append(get_cutover_state(engine, s).as_dict())
        except click.BadParameter:
            # Source not seeded in this environment — report rather than crash.
            states.append({"slug": s, "error": "no data_sources row",
                           "is_firecrawl": False, "legacy_paused": False,
                           "transport_last_14d": {}})

    if as_json:
        click.echo(json.dumps(states, indent=2, default=str))
        return

    click.echo(f"{'source':<12} {'firecrawl':>9} {'legacy_paused':>13}  "
               f"{'transport(14d)':<28} adapter")
    for s in states:
        tcounts = ", ".join(f"{k}={v}" for k, v in s["transport_last_14d"].items()) or "-"
        click.echo(
            f"{s['slug']:<12} {str(s['is_firecrawl']):>9} "
            f"{str(s['legacy_paused']):>13}  {tcounts:<28} "
            f"{s.get('adapter_class') or s.get('error') or ''}"
        )


@click.command(name="cutover-source")
@click.argument("source")
@click.option("--days", default=14, type=int,
              help="Parity-gate window in days (default 14).")
@click.option("--force", is_flag=True,
              help="Cut over even if the parity gate fails (audited override).")
@click.option("--dry-run", is_flag=True,
              help="Show what would change without writing anything.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def cutover_source(source, days, force, dry_run, as_json):
    """Cut a source over to Firecrawl once the parity gate passes.

    Pauses the legacy adapter, repoints ``data_sources.adapter_class`` at the
    Firecrawl discovery pipeline, and writes an audit row. Refuses when the
    14-day parity gate does not pass unless ``--force`` is given. Exits
    non-zero when no cutover happened.
    """
    engine = get_engine()
    result = cutover(engine, source, days=days, force=force, dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        gate = result["gate"]
        click.echo(f"cutover-source: {source}  dry_run={dry_run} force={force}")
        click.echo(f"  gate: {'PASS' if gate['passed'] else 'FAIL'} — {gate['reason']}")
        for a in result["actions"]:
            click.echo(f"  - {a}")
        if result.get("after"):
            after = result["after"]
            click.echo(f"  adapter_class now: {after['adapter_class']}")
            click.echo(f"  transport(14d): {after['transport_last_14d']}")

    raise SystemExit(0 if result["cut_over"] else 1)
