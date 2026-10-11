"""Assemble IntelligenceAnswerV1 from a QueryResult (SPEC-25 §2.2-2.3)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from irc_data.api.schemas.intelligence import IntelligenceAnswerV1, Provenance
from irc_data.api.services.intelligence import evidence as ev
from irc_data.api.services.intelligence.confidence import assess_result
from irc_data.api.services.intelligence.queries import QueryResult, resolve_query


def quality_status(engine: Engine, result: QueryResult) -> str:
    """``incomplete`` > ``degraded`` > ``ok`` (SPEC-25 §2.3)."""
    if result.missing:
        return "incomplete"
    quarantined = ev.active_quarantine_count(engine)
    if (quarantined or 0) > 0 or ev.is_stale(result.freshness_at):
        return "degraded"
    return "ok"


def build_answer(engine: Engine, spec_key: str, result: QueryResult) -> IntelligenceAnswerV1:
    spec = resolve_query(spec_key)
    facts_sha = result.facts_sha or ev.canonical_sha(result.data)
    return IntelligenceAnswerV1(
        query=spec.name,
        version=spec.version,
        data=result.data,
        provenance=Provenance(
            dataset_version=ev.dataset_version(engine),
            facts_sha=facts_sha,
            lineage=list(result.lineage),
            freshness_at=result.freshness_at,
            quality_status=quality_status(engine, result),
        ),
        incomplete=bool(result.missing),
        missing=list(result.missing),
        evidence=list(result.evidence),
        confidence=assess_result(engine, result).model_dump(),
    )


def answer_query(engine: Engine, key: str, params: dict[str, Any]) -> IntelligenceAnswerV1:
    """Run the registered query ``key`` (``<name>.<version>``) and wrap the result.

    Raises :class:`UnknownQuery` for a name/version that is not registered.
    """
    spec = resolve_query(key)
    return build_answer(engine, key, spec.run(engine, params))
