"""Built-in gate validators (DP-05-02).

A *validator* is a pure callable::

    validator(payload, context) -> list[GateFinding]

``payload`` is the stage-specific batch object
(:class:`~irc_data.parsers.extraction_contract.ExtractionBatchV1` for the
extraction gate,
:class:`~irc_data.transform.transformation_contract.TransformationBatchV1`
for the canonical gate, or an :class:`IdentityEffectBatch` for the
identity gate).  ``context`` carries gate options (thresholds).

Each validator returns a list of :class:`GateFinding` — an empty list
means the rule passed.  Findings carry a bounded ``sample`` of offending
records so a reviewer can see the failure without paging the whole
batch.

Rule coverage
-------------

Every rule class in :class:`~irc_data.quality.contracts.RuleClass` is
implemented by at least one validator:

* **extraction gate** — schema identity, provenance (every field cites
  its source), determinism (batch id / extraction hash recompute),
  completeness (non-empty records, known record types), value domain
  (record indices unique and sequential).
* **canonical gate** — schema identity, determinism (transformation id /
  hash / assertion ids recompute), completeness (disjoint assertion /
  reject partition covering every input record), provenance (lineage
  chains), value domain (per-assertion output-schema conformance).
* **identity gate** — identity effects: no self-merges, no cross-type
  merges, merge/split targets well-formed, churn bounded by a
  configurable threshold.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from irc_data.parsers.extraction_contract import (
    ExtractionBatchV1,
    SCHEMA_VERSION as EXTRACTION_SCHEMA_VERSION,
)
from irc_data.quality.contracts import (
    GateFinding,
    GateKind,
    RuleClass,
    canonical_json,
)
from irc_data.transform.transformation_contract import (
    ASSERTION_CONTRACT_VERSION,
    CanonicalAssertionV1,
    TransformationBatchV1,
)


# ---------------------------------------------------------------------------
# Identity-effect payload (the identity gate's input)
# ---------------------------------------------------------------------------


@dataclass
class IdentityEffect:
    """One identity-resolution effect to be gated.

    Attributes
    ----------
    effect_type
        ``"merge"`` | ``"split"`` | ``"new_entity"`` | ``"retract"``.
    entity_type
        The canonical entity type the effect applies to (``boat`` …).
    entity_key
        The primary entity key the effect applies to.
    target_keys
        For merges: the keys being merged *into* ``entity_key``.
        For splits: the keys split *out of* ``entity_key``.
        Empty for ``new_entity`` / ``retract``.
    supersession_id
        For merges: the id of the superseding entity record.
    reason
        Free-form rationale (audit).
    """

    effect_type: str
    entity_type: str
    entity_key: str
    target_keys: list[str] = field(default_factory=list)
    supersession_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_type": self.effect_type,
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "target_keys": list(self.target_keys),
            "supersession_id": self.supersession_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityEffect":
        return cls(
            effect_type=d["effect_type"],
            entity_type=d["entity_type"],
            entity_key=d["entity_key"],
            target_keys=list(d.get("target_keys") or []),
            supersession_id=d.get("supersession_id", ""),
            reason=d.get("reason", ""),
        )


@dataclass
class IdentityEffectBatch:
    """A versioned batch of identity effects to be gated.

    ``batch_id`` is deterministic — derived from the content of the
    effects — so replaying the same effect set is idempotent.
    """

    source_slug: str
    effects: list[IdentityEffect] = field(default_factory=list)
    batch_id: str = ""
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.batch_id:
            raw = canonical_json([e.to_dict() for e in self.effects])
            self.batch_id = f"idfx_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "source_slug": self.source_slug,
            "effects": [e.to_dict() for e in self.effects],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityEffectBatch":
        return cls(
            source_slug=d["source_slug"],
            effects=[IdentityEffect.from_dict(e) for e in d.get("effects", [])],
            batch_id=d.get("batch_id", ""),
            schema_version=d.get("schema_version", "v1"),
        )


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------


#: Validator signature: (payload, context) -> findings.
ValidatorFn = Callable[[Any, dict[str, Any]], list[GateFinding]]


@dataclass(frozen=True)
class GateRule:
    """A registered gate rule.

    ``rule_id`` is stable and dotted: ``"<gate>.<rule_class>.<name>"``.
    """

    rule_id: str
    gate: str
    rule_class: str
    description: str
    fn: ValidatorFn


_RULES: dict[str, list[GateRule]] = {
    GateKind.EXTRACTION.value: [],
    GateKind.CANONICAL.value: [],
    GateKind.IDENTITY.value: [],
}


def register_rule(gate: str, rule_class: str, name: str,
                  description: str) -> Callable[[ValidatorFn], ValidatorFn]:
    """Decorator: register a validator under ``<gate>.<rule_class>.<name>``."""
    def deco(fn: ValidatorFn) -> ValidatorFn:
        rule = GateRule(
            rule_id=f"{gate}.{rule_class}.{name}",
            gate=gate,
            rule_class=rule_class,
            description=description,
            fn=fn,
        )
        _RULES.setdefault(gate, []).append(rule)
        return fn
    return deco


def rules_for(gate: str) -> list[GateRule]:
    """Return the registered rules for *gate* (stable order)."""
    return list(_RULES.get(gate, []))


def all_rules() -> dict[str, list[GateRule]]:
    """Return the full rule registry keyed by gate."""
    return {g: list(rs) for g, rs in _RULES.items()}


# ---------------------------------------------------------------------------
# Extraction gate
# ---------------------------------------------------------------------------


@register_rule(
    GateKind.EXTRACTION.value, RuleClass.SCHEMA.value, "envelope",
    "Extraction envelope identity: versions, ids and hashes present and "
    "well-formed.",
)
def _extraction_schema(batch: ExtractionBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    bad: list[str] = []
    if batch.schema_version != EXTRACTION_SCHEMA_VERSION:
        bad.append(f"schema_version {batch.schema_version!r}")
    if batch.contract_version != EXTRACTION_SCHEMA_VERSION:
        bad.append(f"contract_version {batch.contract_version!r}")
    for attr in ("batch_id", "extraction_hash", "artifact_id",
                 "content_hash", "parser_version", "source_slug"):
        if not getattr(batch, attr, None):
            bad.append(f"{attr} empty")
    if not bad:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.SCHEMA.value}.envelope",
        rule_class=RuleClass.SCHEMA.value,
        gate=GateKind.EXTRACTION.value,
        message="extraction envelope identity fields missing/mismatched",
        sample=sorted(bad),
        failure_count=len(bad),
    )]


@register_rule(
    GateKind.EXTRACTION.value, RuleClass.PROVENANCE.value, "locators",
    "Every extracted field cites its source artifact and content hash.",
)
def _extraction_provenance(batch: ExtractionBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    offenders: list[str] = []
    for rec in batch.records:
        for fld in rec.fields:
            loc = fld.locator
            if (not loc.artifact_id or not loc.content_hash
                    or loc.artifact_id != batch.artifact_id
                    or loc.content_hash != batch.content_hash):
                offenders.append(
                    f"{rec.record_type}[{rec.record_index}].{fld.name}"
                )
    if not offenders:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.PROVENANCE.value}.locators",
        rule_class=RuleClass.PROVENANCE.value,
        gate=GateKind.EXTRACTION.value,
        message="fields with missing/mismatched source locators",
        sample=sorted(offenders),
        failure_count=len(offenders),
    )]


@register_rule(
    GateKind.EXTRACTION.value, RuleClass.DETERMINISM.value, "recompute",
    "batch_id and extraction_hash recompute from content (replay safety).",
)
def _extraction_determinism(batch: ExtractionBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    bad: list[str] = []
    recomputed_batch = batch._derive_batch_id()
    if batch.batch_id != recomputed_batch:
        bad.append(f"batch_id {batch.batch_id!r} != {recomputed_batch!r}")
    recomputed_hash = batch._derive_extraction_hash()
    if batch.extraction_hash != recomputed_hash:
        bad.append("extraction_hash mismatch")
    if not bad:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.DETERMINISM.value}.recompute",
        rule_class=RuleClass.DETERMINISM.value,
        gate=GateKind.EXTRACTION.value,
        message="deterministic ids/hashes do not recompute from content",
        sample=bad,
        failure_count=len(bad),
    )]


@register_rule(
    GateKind.EXTRACTION.value, RuleClass.COMPLETENESS.value, "records",
    "Extraction produced a non-empty, structurally sound record set.",
)
def _extraction_completeness(batch: ExtractionBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if not batch.records:
        findings.append(GateFinding(
            rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.COMPLETENESS.value}.records",
            rule_class=RuleClass.COMPLETENESS.value,
            gate=GateKind.EXTRACTION.value,
            message="extraction batch has zero records",
            sample=[],
            failure_count=1,
        ))
        return findings

    empty_records = [
        f"{r.record_type}[{r.record_index}]"
        for r in batch.records if not r.fields
    ]
    untyped = [
        f"[{r.record_index}]" for r in batch.records if not r.record_type
    ]
    sample = sorted(empty_records + [f"untyped{u}" for u in untyped])
    if sample:
        findings.append(GateFinding(
            rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.COMPLETENESS.value}.records",
            rule_class=RuleClass.COMPLETENESS.value,
            gate=GateKind.EXTRACTION.value,
            message="records with no fields or no record_type",
            sample=sample,
            failure_count=len(sample),
        ))
    return findings


@register_rule(
    GateKind.EXTRACTION.value, RuleClass.VALUE_DOMAIN.value, "indices",
    "Record indices are unique and 0..n-1 sequential within the batch.",
)
def _extraction_indices(batch: ExtractionBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    idx = [r.record_index for r in batch.records]
    problems: list[str] = []
    if len(idx) != len(set(idx)):
        seen, dups = set(), set()
        for i in idx:
            if i in seen:
                dups.add(i)
            seen.add(i)
        problems.append(f"duplicate record_index values: {sorted(dups)}")
    if idx and sorted(idx) != list(range(len(idx))):
        problems.append("record_index not 0..n-1 sequential")
    if not problems:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.EXTRACTION.value}.{RuleClass.VALUE_DOMAIN.value}.indices",
        rule_class=RuleClass.VALUE_DOMAIN.value,
        gate=GateKind.EXTRACTION.value,
        message="record_index domain violation",
        sample=problems,
        failure_count=len(problems),
    )]


# ---------------------------------------------------------------------------
# Canonical gate
# ---------------------------------------------------------------------------


@register_rule(
    GateKind.CANONICAL.value, RuleClass.SCHEMA.value, "envelope",
    "Transformation envelope identity: versions, ids, hashes and lineage "
    "anchors present.",
)
def _canonical_schema(batch: TransformationBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    bad: list[str] = []
    if batch.contract_version != ASSERTION_CONTRACT_VERSION:
        bad.append(f"contract_version {batch.contract_version!r}")
    for attr in ("transformation_id", "transformation_hash",
                 "extraction_batch_id", "extraction_hash",
                 "transformer_name", "transformer_version",
                 "schema_version", "source_slug", "artifact_id",
                 "content_hash"):
        if not getattr(batch, attr, None):
            bad.append(f"{attr} empty")
    if not bad:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.SCHEMA.value}.envelope",
        rule_class=RuleClass.SCHEMA.value,
        gate=GateKind.CANONICAL.value,
        message="transformation envelope identity fields missing/mismatched",
        sample=sorted(bad),
        failure_count=len(bad),
    )]


@register_rule(
    GateKind.CANONICAL.value, RuleClass.DETERMINISM.value, "recompute",
    "transformation_id / transformation_hash / assertion_ids recompute "
    "from content.",
)
def _canonical_determinism(batch: TransformationBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    bad: list[str] = []
    if batch.transformation_id != batch._derive_transformation_id():
        bad.append("transformation_id mismatch")
    if batch.transformation_hash != batch._derive_transformation_hash():
        bad.append("transformation_hash mismatch")
    # Recompute a sample of assertion ids (all of them — cheap).
    for a in batch.assertions:
        expected = CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id=batch.extraction_batch_id,
            record_type=a.lineage.source_record_type,
            record_index=a.lineage.source_record_index,
            transformer_version=a.transformer_version,
            schema_version=a.schema_version,
        )
        if a.assertion_id != expected:
            bad.append(f"assertion_id {a.assertion_id!r} != recomputed")
            if len(bad) >= 25:
                break
    if not bad:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.DETERMINISM.value}.recompute",
        rule_class=RuleClass.DETERMINISM.value,
        gate=GateKind.CANONICAL.value,
        message="deterministic transformation ids/hashes do not recompute",
        sample=bad,
        failure_count=len(bad),
    )]


@register_rule(
    GateKind.CANONICAL.value, RuleClass.COMPLETENESS.value, "partition",
    "Assertions and rejects form a disjoint partition; every assertion "
    "identifies its transformer.",
)
def _canonical_partition(batch: TransformationBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if not batch.asserts_disjoint_partition():
        published = {
            (a.lineage.source_record_type, a.lineage.source_record_index)
            for a in batch.assertions
        }
        rejected = {
            (r.source_record_type, r.source_record_index) for r in batch.rejects
        }
        overlap = sorted(str(x) for x in (published & rejected))
        findings.append(GateFinding(
            rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.COMPLETENESS.value}.partition",
            rule_class=RuleClass.COMPLETENESS.value,
            gate=GateKind.CANONICAL.value,
            message="assertion/reject partition violated (overlap or duplicates)",
            sample=overlap,
            failure_count=len(overlap) or 1,
        ))
    unnamed = [
        a.assertion_id for a in batch.assertions
        if not a.identifies_transformer()
    ]
    if unnamed:
        findings.append(GateFinding(
            rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.COMPLETENESS.value}.partition",
            rule_class=RuleClass.COMPLETENESS.value,
            gate=GateKind.CANONICAL.value,
            message="assertions missing transformer/schema identity",
            sample=sorted(unnamed),
            failure_count=len(unnamed),
        ))
    return findings


@register_rule(
    GateKind.CANONICAL.value, RuleClass.PROVENANCE.value, "lineage",
    "Every assertion carries a complete lineage chain (artifact → "
    "extraction batch → source record).",
)
def _canonical_lineage(batch: TransformationBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    offenders: list[str] = []
    for a in batch.assertions:
        lin = a.lineage
        missing = []
        if not lin.artifact_id or not lin.content_hash:
            missing.append("artifact")
        if not lin.extraction_batch_id or not lin.extraction_hash:
            missing.append("extraction_batch")
        if not lin.source_slug:
            missing.append("source_slug")
        if lin.source_record_type is None or lin.source_record_index is None:
            missing.append("source_record")
        if missing:
            offenders.append(f"{a.assertion_id}: missing {','.join(missing)}")
    if not offenders:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.PROVENANCE.value}.lineage",
        rule_class=RuleClass.PROVENANCE.value,
        gate=GateKind.CANONICAL.value,
        message="assertions with incomplete lineage",
        sample=sorted(offenders),
        failure_count=len(offenders),
    )]


@register_rule(
    GateKind.CANONICAL.value, RuleClass.VALUE_DOMAIN.value, "output_schema",
    "Every assertion's payload conforms to its registered output schema.",
)
def _canonical_output_schema(batch: TransformationBatchV1, ctx: dict[str, Any]) -> list[GateFinding]:
    # Import here to keep module import cheap and avoid a hard dependency
    # when only the extraction / identity gates are exercised.
    from irc_data.transform.schemas import get_assertion_schema
    from irc_data.transform.transformation_contract import (
        UnknownAssertionSchemaError,
    )

    offenders: list[str] = []
    for a in batch.assertions:
        try:
            schema = get_assertion_schema(a.assertion_type, a.schema_version)
            schema.model_validate(a.data)
        except UnknownAssertionSchemaError:
            # Unknown schema = pipeline misconfiguration → schema-class
            # finding, surfaced separately.
            offenders.append(f"{a.assertion_id}: unknown schema "
                             f"({a.assertion_type},{a.schema_version})")
        except Exception as exc:  # pydantic.ValidationError
            offenders.append(f"{a.assertion_id}: {type(exc).__name__}")
        if len(offenders) >= 25:
            break
    if not offenders:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.CANONICAL.value}.{RuleClass.VALUE_DOMAIN.value}.output_schema",
        rule_class=RuleClass.VALUE_DOMAIN.value,
        gate=GateKind.CANONICAL.value,
        message="assertions failing registered output-schema validation",
        sample=sorted(offenders),
        failure_count=len(offenders),
    )]


# ---------------------------------------------------------------------------
# Identity gate
# ---------------------------------------------------------------------------

_VALID_EFFECT_TYPES = {"merge", "split", "new_entity", "retract"}


@register_rule(
    GateKind.IDENTITY.value, RuleClass.IDENTITY_EFFECT.value, "shape",
    "Effects are well-formed: known type, entity identity present, "
    "merge/split carry targets.",
)
def _identity_shape(batch: IdentityEffectBatch, ctx: dict[str, Any]) -> list[GateFinding]:
    offenders: list[str] = []
    for e in batch.effects:
        problems: list[str] = []
        if e.effect_type not in _VALID_EFFECT_TYPES:
            problems.append(f"unknown effect_type {e.effect_type!r}")
        if not e.entity_type or not e.entity_key:
            problems.append("entity identity missing")
        if e.effect_type in ("merge", "split") and not e.target_keys:
            problems.append(f"{e.effect_type} with no targets")
        if problems:
            offenders.append(f"{e.effect_type}:{e.entity_key} ({'; '.join(problems)})")
    if not offenders:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.IDENTITY.value}.{RuleClass.IDENTITY_EFFECT.value}.shape",
        rule_class=RuleClass.IDENTITY_EFFECT.value,
        gate=GateKind.IDENTITY.value,
        message="malformed identity effects",
        sample=sorted(offenders),
        failure_count=len(offenders),
    )]


@register_rule(
    GateKind.IDENTITY.value, RuleClass.IDENTITY_EFFECT.value, "self_merge",
    "No effect merges or splits an entity with itself.",
)
def _identity_self_merge(batch: IdentityEffectBatch, ctx: dict[str, Any]) -> list[GateFinding]:
    offenders = sorted(
        f"{e.effect_type}:{e.entity_type}/{e.entity_key}"
        for e in batch.effects
        if e.effect_type in ("merge", "split") and e.entity_key in e.target_keys
    )
    if not offenders:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.IDENTITY.value}.{RuleClass.IDENTITY_EFFECT.value}.self_merge",
        rule_class=RuleClass.IDENTITY_EFFECT.value,
        gate=GateKind.IDENTITY.value,
        message="self-merge/self-split effects detected",
        sample=offenders,
        failure_count=len(offenders),
    )]


@register_rule(
    GateKind.IDENTITY.value, RuleClass.IDENTITY_EFFECT.value, "duplicate_effects",
    "No two effects in the batch are identical (replay safety).",
)
def _identity_duplicates(batch: IdentityEffectBatch, ctx: dict[str, Any]) -> list[GateFinding]:
    seen: dict[str, int] = {}
    for e in batch.effects:
        key = canonical_json(e.to_dict())
        seen[key] = seen.get(key, 0) + 1
    dups = sorted(k for k, n in seen.items() if n > 1)
    if not dups:
        return []
    return [GateFinding(
        rule_id=f"{GateKind.IDENTITY.value}.{RuleClass.IDENTITY_EFFECT.value}.duplicate_effects",
        rule_class=RuleClass.IDENTITY_EFFECT.value,
        gate=GateKind.IDENTITY.value,
        message="duplicate identity effects in one batch",
        sample=[d[:120] for d in dups],
        failure_count=len(dups),
    )]


@register_rule(
    GateKind.IDENTITY.value, RuleClass.VALUE_DOMAIN.value, "churn",
    "Identity churn is bounded: effects count does not exceed the "
    "configured threshold (default 1000).",
)
def _identity_churn(batch: IdentityEffectBatch, ctx: dict[str, Any]) -> list[GateFinding]:
    threshold = int(ctx.get("max_effects", 1000))
    n = len(batch.effects)
    if n <= threshold:
        return []
    sample = [
        f"{e.effect_type}:{e.entity_type}/{e.entity_key}"
        for e in batch.effects[:25]
    ]
    return [GateFinding(
        rule_id=f"{GateKind.IDENTITY.value}.{RuleClass.VALUE_DOMAIN.value}.churn",
        rule_class=RuleClass.VALUE_DOMAIN.value,
        gate=GateKind.IDENTITY.value,
        message=f"identity churn {n} exceeds threshold {threshold}",
        sample=sample,
        failure_count=n - threshold,
    )]


__all__ = [
    "GateRule",
    "IdentityEffect",
    "IdentityEffectBatch",
    "ValidatorFn",
    "all_rules",
    "register_rule",
    "rules_for",
]
