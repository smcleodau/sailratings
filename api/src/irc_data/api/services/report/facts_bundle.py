"""SM-01-08 — ReportFactsV1 bundle emitter.

The single deterministic contract between the quantitative engines
(``irc_data.analysis`` + ``irc_data.api.services.report.facts_builders``)
and the narrative generator (AI-01-06). Every number the report prose is
allowed to cite flows through this bundle, so the bundle is also what the
golden-fixture harness (``irc_data.analysis.backtest``) snapshots to prove
that model changes do not silently move report figures.

Bundle layout (JSON-serialised with ``schema_version`` "ReportFactsV1")::

    {
      "schema_version": "ReportFactsV1",
      "boat": {"id", "name", "sail_number", "design"},
      "sections": {
        "s01_executive":        {...},   # ExecutiveSummaryFacts
        "s02_identity":         {...},   # IdentityFacts
        "s03_rating_anatomy":   {...},   # RatingAnatomyFacts
        "s04_rating_evolution": {...},   # RatingEvolutionFacts
        "s05_class_context":    {...},   # ClassContextFacts
        "s06_performance":      {...},   # PerformanceFacts
        "s07_sensitivity":      {...},   # SensitivityFacts
        "s08_optimisation":     {...},   # OptimisationFacts
        "s09_formula_drift":    {...},   # FormulaDriftFacts
        "s10_rivals":           {...},   # RivalsFacts
        "s11_appendix":         {...},   # AppendixFacts
      },
      "engines": {                        # straight from the analysis layer
        "rai":              {...},        # RAIResult.to_dict()
        "design_model":     {...},        # RegressionResult.to_dict()
        "fleet_wide_model": {...},        # Tier-C RegressionResult.to_dict()
        "smart_boats":      {...},        # get_smart_boats()
      },
      "facts_sha256": "...",              # hash of canonical sections+engines
    }

Determinism rules (this is what makes golden fixtures meaningful):

* floats are rounded to a fixed number of decimals per container type;
* ``Decimal``/``date``/``datetime`` are rendered as plain strings/numbers;
* lists whose order is not semantically stable (head-to-head rivals,
  recent results) are re-sorted on explicit keys;
* JSON is dumped with ``sort_keys=True`` so the byte stream is stable.

``validate_report_facts_bundle`` performs a lightweight shape check so the
narrative generator (and CI) can fail fast on a malformed bundle.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import fields as dc_fields
from dataclasses import is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine

from irc_data.api.services.report import facts_builders

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "ReportFactsV1"

# Canonical section order == report page order (mirrors orchestrator.SECTION_MODULES).
SECTION_ORDER: tuple[str, ...] = (
    "s01_executive",
    "s02_identity",
    "s03_rating_anatomy",
    "s04_rating_evolution",
    "s05_class_context",
    "s06_performance",
    "s07_sensitivity",
    "s08_optimisation",
    "s09_formula_drift",
    "s10_rivals",
    "s11_appendix",
)

# Section id -> facts builder. Pure mapping; no LLM involved anywhere here.
_BUILDERS = {
    "s01_executive": facts_builders.build_executive_summary,
    "s02_identity": facts_builders.build_identity,
    "s03_rating_anatomy": facts_builders.build_rating_anatomy,
    "s04_rating_evolution": facts_builders.build_rating_evolution,
    "s05_class_context": facts_builders.build_class_context,
    "s06_performance": facts_builders.build_performance,
    "s07_sensitivity": facts_builders.build_sensitivity,
    "s08_optimisation": facts_builders.build_optimisation,
    "s09_formula_drift": facts_builders.build_formula_drift,
    "s10_rivals": facts_builders.build_rivals,
    "s11_appendix": facts_builders.build_appendix,
}

# Figures the golden harness must reproduce within this absolute tolerance.
# TCC-scale numbers are stated to 3dp by the product, so 5e-3 guards against
# rounding-level jitter while still catching real model movement.
DEFAULT_ABS_TOL = 5e-3


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _round_float(v: float) -> float:
    # 6dp keeps full TCC-scale precision (TCC is quoted to 3dp) while
    # killing off scipy/sklearn last-bit jitter across platforms.
    return round(float(v), 6)


def _normalise(obj: Any) -> Any:
    """Recursively convert a Facts dataclass tree into canonical JSON-able
    form: Decimals/dates as strings, floats rounded, dict keys sorted on
    dump. Lists keep their order here — semantic re-sorting happens in
    ``_stabilise`` below."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _normalise(getattr(obj, f.name)) for f in dc_fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, float):
        return _round_float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def _stabilise(section_id: str, data: dict) -> dict:
    """Re-sort list fields whose emission order is an implementation detail
    so two runs over identical data always byte-match."""
    def _by_key(rows: list, keys: tuple[str, ...]) -> list:
        return sorted(
            rows,
            key=lambda r: tuple(str(r.get(k) or "") for k in keys),
        )

    if section_id == "s02_identity":
        if data.get("identities"):
            data["identities"] = _by_key(
                data["identities"], ("observed_date", "boat_name", "sail_number", "source")
            )
        if data.get("skipper_stints"):
            data["skipper_stints"] = _by_key(data["skipper_stints"], ("name",))
    elif section_id == "s03_rating_anatomy":
        # Keep the impact ordering (biggest |contrib| first) but break ties
        # deterministically on the field name.
        if data.get("decomposition"):
            data["decomposition"] = sorted(
                data["decomposition"],
                key=lambda c: (-abs(c.get("contrib_tcc") or 0.0), c.get("field") or ""),
            )
    elif section_id == "s04_rating_evolution":
        if data.get("snapshots"):
            data["snapshots"] = _by_key(data["snapshots"], ("date", "source"))
        if data.get("cert_reissue_dates"):
            data["cert_reissue_dates"] = sorted(data["cert_reissue_dates"])
    elif section_id == "s05_class_context":
        if data.get("top_5_boats"):
            data["top_5_boats"] = _by_key(data["top_5_boats"], ("name", "sail"))
        if data.get("class_tcc_list"):
            data["class_tcc_list"] = sorted(data["class_tcc_list"])
    elif section_id == "s06_performance":
        if data.get("recent_results"):
            data["recent_results"] = _by_key(
                data["recent_results"], ("event_date", "event_name", "race_name")
            )
        if data.get("head_to_head"):
            data["head_to_head"] = _by_key(data["head_to_head"], ("name", "sail_number"))
    elif section_id == "s07_sensitivity":
        if data.get("coefficients"):
            data["coefficients"] = sorted(
                data["coefficients"], key=lambda c: c.get("field") or ""
            )
    elif section_id == "s08_optimisation":
        if data.get("recommendations"):
            data["recommendations"] = sorted(
                data["recommendations"],
                key=lambda r: (r.get("est_tcc_gain") or 0.0, r.get("measurement") or ""),
            )
    elif section_id == "s09_formula_drift":
        if data.get("affected_measurements"):
            data["affected_measurements"] = sorted(data["affected_measurements"])
    elif section_id == "s10_rivals":
        if data.get("rivals"):
            data["rivals"] = _by_key(data["rivals"], ("name", "sail_number"))
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_report_facts(
    engine: Engine,
    boat_id: int,
    *,
    include_engines: bool = True,
) -> dict:
    """Build the full ReportFactsV1 bundle for one boat.

    Pure w.r.t. the database: given identical table contents and an
    unchanged codebase, the emitted bundle is byte-identical. No LLM calls
    are made — this is the numeric substrate the narrative generator
    (AI-01-06) renders into prose.
    """
    sections: dict[str, dict] = {}
    boat_meta: dict[str, Any] = {"id": boat_id, "name": None, "sail_number": None, "design": None}

    for section_id in SECTION_ORDER:
        builder = _BUILDERS[section_id]
        try:
            facts = builder(engine, boat_id)
            data = _stabilise(section_id, _normalise(facts))
        except Exception as e:  # a dead engine must not sink the bundle
            logger.warning("facts builder for %s failed: %s", section_id, e)
            data = {"_error": str(e)}
        sections[section_id] = data

        # Populate bundle-level identity from whichever section knows it.
        if boat_meta["name"] is None and isinstance(data, dict) and data.get("boat_name"):
            boat_meta["name"] = data.get("boat_name")
        if boat_meta["sail_number"] is None and isinstance(data, dict) and data.get("sail_number"):
            boat_meta["sail_number"] = data.get("sail_number")
        if boat_meta["design"] is None and isinstance(data, dict) and data.get("design"):
            boat_meta["design"] = data.get("design")

    engines: dict[str, Any] = {}
    if include_engines:
        engines = _build_engine_block(engine, boat_id, boat_meta.get("design"))

    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "boat": boat_meta,
        "sections": sections,
        "engines": engines,
    }
    bundle["facts_sha256"] = hashlib.sha256(
        json.dumps(
            {"sections": sections, "engines": engines},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return bundle


def _build_engine_block(engine: Engine, boat_id: int, design: str | None) -> dict:
    """The analysis-engine outputs, verbatim (post-normalisation)."""
    from irc_data.analysis.performance import compute_rai, get_smart_boats
    from irc_data.analysis.regression import analyze_design_sensitivity, run_tier_c_model

    engines: dict[str, Any] = {}

    try:
        rai = compute_rai(engine, boat_id)
        engines["rai"] = _normalise(rai.to_dict()) if rai else None
    except Exception as e:
        logger.warning("engine rai failed: %s", e)
        engines["rai"] = {"_error": str(e)}

    if design:
        try:
            sens = analyze_design_sensitivity(engine, design)
            engines["design_model"] = _normalise(sens.to_dict()) if sens else None
        except Exception as e:
            logger.warning("engine design_model failed: %s", e)
            engines["design_model"] = {"_error": str(e)}

        try:
            engines["smart_boats"] = _normalise(get_smart_boats(engine, design))
        except Exception as e:
            logger.warning("engine smart_boats failed: %s", e)
            engines["smart_boats"] = {"_error": str(e)}
    else:
        engines["design_model"] = None
        engines["smart_boats"] = None

    try:
        tier_c = run_tier_c_model(engine)
        engines["fleet_wide_model"] = _normalise(tier_c.to_dict()) if tier_c else None
    except Exception as e:
        logger.warning("engine fleet_wide_model failed: %s", e)
        engines["fleet_wide_model"] = {"_error": str(e)}

    return engines


def bundle_to_json(bundle: dict) -> str:
    """Canonical JSON serialisation (sorted keys, fixed rounding)."""
    return json.dumps(bundle, sort_keys=True, indent=2, default=str)


def validate_report_facts_bundle(bundle: dict) -> list[str]:
    """Lightweight structural validation. Returns a list of violations;
    an empty list means the bundle is a well-formed ReportFactsV1."""
    violations: list[str] = []

    if bundle.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {bundle.get('schema_version')!r}"
        )

    boat = bundle.get("boat")
    if not isinstance(boat, dict) or "id" not in boat:
        violations.append("bundle.boat must be an object with at least an 'id'")

    sections = bundle.get("sections")
    if not isinstance(sections, dict):
        violations.append("bundle.sections must be an object")
    else:
        for section_id in SECTION_ORDER:
            if section_id not in sections:
                violations.append(f"missing section {section_id!r}")
            elif not isinstance(sections[section_id], dict):
                violations.append(f"section {section_id!r} must be an object")

    engines = bundle.get("engines")
    if not isinstance(engines, dict):
        violations.append("bundle.engines must be an object")

    digest = bundle.get("facts_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        violations.append("bundle.facts_sha256 must be a 64-char hex string")

    return violations
