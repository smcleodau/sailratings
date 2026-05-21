"""Run every section in parallel, aggregate into one report payload."""
from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from sqlalchemy.engine import Engine

from irc_data.api.services.report.sections import (
    s01_executive, s02_identity, s03_rating_anatomy, s04_rating_evolution,
    s05_class_context, s06_performance, s07_sensitivity, s08_optimisation,
    s09_formula_drift, s10_rivals, s11_appendix,
)
from irc_data.api.services.report.sections._base import SectionResult

logger = logging.getLogger(__name__)

# Order = order on the page.
SECTION_MODULES = [
    s01_executive, s02_identity, s03_rating_anatomy, s04_rating_evolution,
    s05_class_context, s06_performance, s07_sensitivity, s08_optimisation,
    s09_formula_drift, s10_rivals, s11_appendix,
]


def build_report(engine: Engine, boat_id: int) -> dict:
    """Run all sections in parallel. Returns one dict ready for the
    Jinja2 template + the report_analytics JSONB column."""
    results: dict[str, SectionResult] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_safe_generate, mod, engine, boat_id): mod
            for mod in SECTION_MODULES
        }
        for fut in as_completed(futures):
            mod = futures[fut]
            try:
                results[mod.SECTION_ID] = fut.result()
            except Exception as e:
                logger.exception("section %s crashed: %s", mod.SECTION_ID, e)
                results[mod.SECTION_ID] = SectionResult(
                    section_id=mod.SECTION_ID, title=mod.SECTION_TITLE,
                    markdown="", chart_pngs={}, structured={}, error=str(e),
                )

    ordered = [results[m.SECTION_ID] for m in SECTION_MODULES]
    return {
        "boat_id": boat_id,
        "sections": [_section_to_dict(s) for s in ordered],
    }


def _safe_generate(mod, engine: Engine, boat_id: int) -> SectionResult:
    return mod.generate(engine, boat_id)


def _section_to_dict(s: SectionResult) -> dict:
    return {
        "section_id": s.section_id,
        "title": s.title,
        "markdown": s.markdown,
        # base64 charts so the JSON blob is self-contained.
        "chart_pngs_b64": {k: base64.b64encode(v).decode() for k, v in s.chart_pngs.items()},
        "structured": s.structured,
        "error": s.error,
    }
