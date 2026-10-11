"""Registry of named, versioned intelligence queries (SPEC-25 §2.1).

Each query wraps an *existing* service call (router function or
``build_report_facts``) without changing its response, and reports what it
found as a :class:`QueryResult`. ``answers.py`` turns a ``QueryResult`` into an
``IntelligenceAnswerV1`` by attaching provenance.

Incomplete-data rules (reason codes are the SPEC-25 §1.4 vocabulary where one
exists) live next to each query. A query never raises for missing input: it
returns what it has plus ``missing`` reason codes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.engine import Engine

from irc_data.api.schemas.intelligence import EvidenceRef
from irc_data.api.services.intelligence import evidence as ev


class UnknownQuery(KeyError):
    """Raised when a query name / version is not in the registry."""


@dataclass
class QueryResult:
    data: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    freshness_at: datetime | None = None
    # Set when the underlying contract already carries a hash (ReportFactsV1).
    facts_sha: str | None = None


@dataclass(frozen=True)
class QuerySpec:
    name: str
    version: str
    description: str
    required: tuple[str, ...]
    run: Callable[[Engine, dict[str, Any]], QueryResult]

    @property
    def key(self) -> str:
        return f"{self.name}.{self.version}"


# ---------------------------------------------------------------------------
# Wrapped service calls - the only place the existing code is touched. Tests
# replace these (PostgreSQL-only SQL cannot run on the SQLite fixture).
# ---------------------------------------------------------------------------


def fetch_boat(engine: Engine, boat_id: int) -> dict:
    from irc_data.api.routers import boats

    return boats.get_boat(boat_id, engine)


def fetch_search(engine: Engine, q: str, limit: int, offset: int) -> dict:
    from irc_data.api.routers import search

    return search.fuzzy_search(q=q, limit=limit, offset=offset, engine=engine)


def fetch_design_fleet(engine: Engine, design: str, sort: str, limit: int, offset: int) -> dict:
    from irc_data.api.routers import designs

    return designs.get_design_fleet(
        design_name=design, sort=sort, limit=limit, offset=offset, engine=engine
    )


def fetch_report_facts(engine: Engine, boat_id: int) -> dict:
    from irc_data.api.services.report.facts_bundle import build_report_facts

    return build_report_facts(engine, boat_id)


# ---------------------------------------------------------------------------
# Param helpers
# ---------------------------------------------------------------------------


def _int(params: dict[str, Any], key: str) -> int | None:
    value = params.get(key)
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _str(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    value = str(value).strip() if value is not None else ""
    return value or None


# ---------------------------------------------------------------------------
# boat_profile.v1
# ---------------------------------------------------------------------------


def run_boat_profile(engine: Engine, params: dict[str, Any]) -> QueryResult:
    boat_id = _int(params, "boat_id")
    if boat_id is None:
        return QueryResult(missing=["boat_id"], lineage=[ev.table_ref("boats")])

    try:
        row = fetch_boat(engine, boat_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return QueryResult(missing=["boat_not_found"], lineage=[ev.table_ref("boats", id=boat_id)])

    missing: list[str] = []
    lineage = [ev.table_ref("boats", id=boat_id)]
    evidence = [ev.evidence("boats", lineage[0], "boat row", row.get("boat_name"))]

    has_irc = row.get("irc_tcc") is not None
    has_orc = row.get("orc_gph") is not None
    if has_irc:
        ref = ev.table_ref("tcc_snapshots", boat_id=boat_id, snapshot_date=row.get("irc_snapshot_date"))
        lineage.append(ref)
        evidence.append(ev.evidence("tcc_snapshots", ref, "latest IRC TCC", row.get("irc_tcc")))
    if has_orc:
        ref = ev.table_ref("orc_certificates", boat_id=boat_id, snapshot_date=row.get("orc_snapshot_date"))
        lineage.append(ref)
        evidence.append(ev.evidence("orc_certificates", ref, "latest ORC GPH", row.get("orc_gph")))
    if not (has_irc or has_orc):
        missing.append("no_current_certificate")
    if not (row.get("design_canonical") or row.get("design")):
        missing.append("no_design_class")

    return QueryResult(
        data=row,
        missing=missing,
        lineage=lineage,
        evidence=evidence,
        freshness_at=ev.latest(
            row.get("updated_at"), row.get("irc_snapshot_date"), row.get("orc_snapshot_date")
        ),
    )


# ---------------------------------------------------------------------------
# rating_book_search.v1
# ---------------------------------------------------------------------------


def run_rating_book_search(engine: Engine, params: dict[str, Any]) -> QueryResult:
    q = _str(params, "q")
    if q is None:
        return QueryResult(missing=["q"], lineage=[ev.table_ref("boats")])

    limit = _int(params, "limit") or 20
    offset = _int(params, "offset") or 0
    payload = fetch_search(engine, q, limit, offset)

    ids = [r["id"] for r in payload.get("results", []) if r.get("id") is not None]
    lineage = [ev.table_ref("boats")] + [ev.table_ref("boats", id=i) for i in ids]
    evidence = [
        ev.evidence("boats", ev.table_ref("boats", id=r["id"]), "search hit", r.get("boat_name"))
        for r in payload.get("results", [])
        if r.get("id") is not None
    ]
    return QueryResult(
        data=payload,
        lineage=lineage,
        evidence=evidence,
        freshness_at=ev.boats_freshness(engine, ids),
    )


# ---------------------------------------------------------------------------
# fleet_explorer.v1
# ---------------------------------------------------------------------------


def run_fleet_explorer(engine: Engine, params: dict[str, Any]) -> QueryResult:
    design = _str(params, "design")
    if design is None:
        return QueryResult(missing=["design"], lineage=[ev.table_ref("boats")])

    sort = _str(params, "sort") or "tcc"
    if sort not in ("tcc", "name", "country"):
        return QueryResult(missing=["sort"], lineage=[ev.table_ref("boats", design=design)])
    limit = _int(params, "limit") or 100
    offset = _int(params, "offset") or 0

    try:
        payload = fetch_design_fleet(engine, design, sort, limit, offset)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return QueryResult(
            missing=["design_not_found"], lineage=[ev.table_ref("boats", design=design)]
        )

    from irc_data.analysis.class_regression import MIN_BOATS

    missing: list[str] = []
    if payload.get("total", 0) < MIN_BOATS:
        missing.append("class_too_small")

    rows = payload.get("results", [])
    ids = [r["id"] for r in rows if r.get("id") is not None]
    lineage = [ev.table_ref("boats", design=design), ev.table_ref("tcc_snapshots", design=design)]
    lineage += [ev.table_ref("boats", id=i) for i in ids]
    evidence = [
        ev.evidence("boats", ev.table_ref("boats", id=r["id"]), "fleet member", r.get("boat_name"))
        for r in rows
        if r.get("id") is not None
    ]
    return QueryResult(
        data=payload,
        missing=missing,
        lineage=lineage,
        evidence=evidence,
        freshness_at=ev.latest(
            ev.boats_freshness(engine, ids), *[r.get("rating_date") for r in rows]
        ),
    )


# ---------------------------------------------------------------------------
# report_facts.v1
# ---------------------------------------------------------------------------


def run_report_facts(engine: Engine, params: dict[str, Any]) -> QueryResult:
    boat_id = _int(params, "boat_id")
    if boat_id is None:
        return QueryResult(missing=["boat_id"], lineage=[ev.table_ref("boats")])

    boat_ref = ev.table_ref("boats", id=boat_id)
    boat_updated = ev.boats_freshness(engine, [boat_id])
    if boat_updated is None:
        return QueryResult(missing=["boat_not_found"], lineage=[boat_ref])

    bundle = fetch_report_facts(engine, boat_id)
    sections = bundle.get("sections") or {}
    engines = bundle.get("engines") or {}

    missing: list[str] = []
    if not (bundle.get("boat") or {}).get("design"):
        missing.append("no_design_class")
    missing += [f"section:{k}" for k, v in sorted(sections.items()) if isinstance(v, dict) and "_error" in v]
    missing += [f"engine:{k}" for k, v in sorted(engines.items()) if isinstance(v, dict) and "_error" in v]
    if "rai" in engines and engines["rai"] is None:
        missing.append("insufficient_races_for_rai")

    lineage = [boat_ref, f"report_facts:{bundle.get('schema_version')}:boat_id={boat_id}"]
    lineage += [f"report_facts:section={k}" for k in sorted(sections)]
    evidence = [
        ev.evidence("report_facts", f"report_facts:section={k}", k)
        for k in sorted(sections)
        if not (isinstance(sections[k], dict) and "_error" in sections[k])
    ]
    return QueryResult(
        data=bundle,
        missing=missing,
        lineage=lineage,
        evidence=evidence,
        freshness_at=boat_updated,
        facts_sha=bundle.get("facts_sha256"),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SPECS = (
    QuerySpec("boat_profile", "v1", "Boat detail with latest IRC/ORC ratings.", ("boat_id",), run_boat_profile),
    QuerySpec("rating_book_search", "v1", "Fuzzy search of the rating book by name or sail number.", ("q",), run_rating_book_search),
    QuerySpec("fleet_explorer", "v1", "Boats of one design class with current IRC ratings.", ("design",), run_fleet_explorer),
    QuerySpec("report_facts", "v1", "ReportFactsV1 bundle for one boat.", ("boat_id",), run_report_facts),
)

REGISTRY: dict[str, QuerySpec] = {spec.key: spec for spec in _SPECS}


def resolve_query(key: str) -> QuerySpec:
    """Look up ``<name>.<version>`` (e.g. ``boat_profile.v1``)."""
    try:
        return REGISTRY[key]
    except KeyError:
        raise UnknownQuery(key) from None
