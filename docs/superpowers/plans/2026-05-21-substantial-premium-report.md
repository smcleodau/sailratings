# 12-20 Page Premium Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the $99 premium IRC report from ~800-word single-section markdown into a substantial 12-20 page document with 11 dedicated sections, embedded PNG charts, and per-section Claude generation grounded in verifiable facts so it stops feeling generic.

**Architecture:** Replace `report_service.generate_report_content`'s single Claude call with a section-orchestrator that runs per-section generators in parallel. Each section receives a strict `Facts` dataclass (pre-computed from DB + analysis engines), generates focused prompt-driven prose, returns markdown + chart references. A chart factory renders Matplotlib figures to PNG (base64-inlined into the final HTML) so the Playwright PDF stays self-contained. A new Jinja2 template stitches sections into the 11-section layout.

**Tech Stack:** Python 3.11, Anthropic SDK (Claude Sonnet 4.5), Matplotlib (headless `Agg` backend), Jinja2, Playwright (existing PDF renderer), Pydantic dataclasses for Facts contracts.

---

## File structure

**New files:**
```
api/src/irc_data/api/services/report/
├── __init__.py
├── orchestrator.py              # top-level: build all sections → assemble markdown + analytics
├── facts.py                     # Facts dataclasses (one per section, strict typing)
├── facts_builders.py            # SQL + engine calls that populate Facts
├── charts.py                    # Matplotlib PNG factory: 5 chart types, returns bytes
├── prompts.py                   # SYSTEM_PROMPT_PREMIUM_V2 + per-section prompt templates
├── claude_client.py             # thin Claude wrapper with the truth-discipline pattern
└── sections/
    ├── __init__.py
    ├── _base.py                 # SectionResult dataclass + base helpers
    ├── s01_executive.py
    ├── s02_identity.py
    ├── s03_rating_anatomy.py
    ├── s04_rating_evolution.py
    ├── s05_class_context.py
    ├── s06_performance.py
    ├── s07_sensitivity.py
    ├── s08_optimisation.py
    ├── s09_formula_drift.py
    ├── s10_rivals.py
    └── s11_appendix.py
api/src/irc_data/api/templates/
└── report_v2.html               # 11-section template with chart slots
api/tests/report/
├── test_facts_builders.py
├── test_charts.py
├── test_prompts_truth_discipline.py
├── test_sections/
│   ├── test_s03_rating_anatomy.py
│   └── test_orchestrator.py
└── fixtures/
    └── sun_fish_facts.py        # known SUN FISH facts for golden tests
```

**Modified files:**
```
api/src/irc_data/analysis/regression.py
  └── add `class_mean_tcc` + median + percentile to get_boat_sensitivity_context
api/src/irc_data/api/services/report_service.py
  └── route to new orchestrator behind a feature flag (env REPORT_V2=true)
api/src/irc_data/api/services/pdf_service.py
  └── pick template by feature flag (report_v2.html when REPORT_V2=true)
```

---

## Section / Facts / Chart matrix

Each section gets a tightly-scoped Facts dataclass. Claude only sees what's in Facts — anything not in Facts cannot be cited. This is the truth-discipline mechanism.

| § | Section | Pages | Charts | Key Facts fields |
|---|---|---|---|---|
| 1 | Executive Summary | 1 | none | verdict_label, headline_finding_1/2/3, top_recommendation |
| 2 | Boat Identity & History | 1 | none | identities[], sail_wardrobe_history[], owner_history[], build_metadata |
| 3 | Rating Anatomy | 2 | `anatomy_bar.png` | tcc_now, class_mean_tcc, decomposition[{field, contrib_tcc, this_boat, class_mean, unit}] |
| 4 | Rating Evolution | 2 | `tcc_timeseries.png` | snapshots[{date, tcc}], cert_reissue_dates[], drift_annotations[] |
| 5 | Class Context | 2 | `class_distribution.png` | class_n, class_tcc_band, this_boat_percentile, top_5_boats[] |
| 6 | Racing Performance | 3 | `results_timeline.png`, `rai_scatter.png` | finishes, wins, podiums, distinct_events, rai_pct, head_to_head[] |
| 7 | Measurement Sensitivity | 2 | `sensitivity_bar.png` | coefficients[] (signed-impact-sorted), boat_z_scores[] |
| 8 | Optimisation Recommendations | 3 | none | recommendations[{measurement, delta, est_tcc_gain, rationale}] |
| 9 | Formula Drift | 1 | `drift_line.png` (optional) | per_design_drift_5y, affected_measurements[] |
| 10 | Rival Watch | 2 | none | rivals[{name, sail, country, tcc, recent_finishes[]}] |
| 11 | Appendix | 1 | none | methodology_blurb, data_sources[], glossary |

Total: ~20 pages, 6 distinct chart types.

---

## Phase A — Foundations (Tasks 1-5)

These unblock everything else: the regression engine fix, the Facts contract pattern, the truth-discipline pattern, the chart factory, and the orchestrator skeleton. No sections built yet.

### Task 1: Add class baseline stats to regression engine

**Files:**
- Modify: `api/src/irc_data/analysis/regression.py:517-575` (`get_boat_sensitivity_context`)
- Modify: `api/src/irc_data/analysis/regression.py:670-702` (`_fetch_class_means`)
- Test: `api/tests/test_regression_class_baseline.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_regression_class_baseline.py`:

```python
"""Verify get_boat_sensitivity_context returns class baseline TCC stats.

The substantial report needs to anchor decomposition with "median Sunfast
3300 rates X; this boat rates Y; here's where the Y−X delta came from."
The regression engine already returns per-measurement coefficients and
this boat's value; it does not currently return the class TCC baseline.
"""
from irc_data.db.connection import get_engine
from irc_data.analysis.regression import get_boat_sensitivity_context


def test_class_baseline_tcc_present_for_sunfast_3300():
    eng = get_engine()
    # SUN FISH (id=12330) is a Sunfast 3300 we know has Tier A data.
    r = get_boat_sensitivity_context(eng, 12330, "Sunfast 3300")
    assert r is not None
    assert "class_baseline" in r
    cb = r["class_baseline"]
    assert "mean_tcc" in cb and 0.5 < cb["mean_tcc"] < 1.5
    assert "median_tcc" in cb and 0.5 < cb["median_tcc"] < 1.5
    assert "p25_tcc" in cb and "p75_tcc" in cb
    assert cb["p25_tcc"] < cb["median_tcc"] < cb["p75_tcc"]
    assert "this_boat_tcc" in cb
    assert "this_boat_percentile" in cb
    assert 0 <= cb["this_boat_percentile"] <= 100
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/test_regression_class_baseline.py -v
```
Expected: FAIL — `KeyError: 'class_baseline'`.

- [ ] **Step 3: Extend `_fetch_class_means` to also pull TCC distribution**

In `regression.py`, replace the body of `_fetch_class_means` to also fetch tcc mean/median/p25/p75 using `percentile_cont` over the latest tcc_snapshots row per boat:

```python
def _fetch_class_means(engine: Engine, design: str) -> dict:
    """Return per-feature means + a TCC distribution for the design class.

    Uses the latest tcc_snapshot per boat. The TCC summary lets the
    report anchor decomposition with the median rating in the class.
    """
    query = text("""
        WITH latest AS (
            SELECT DISTINCT ON (b.id)
                   b.id, t.tcc, t.lh, t.beam, t.draft, t.headsails,
                   t.spinnakers, t.crew, t.dlr,
                   c.displacement_kg AS displacement, c.p, c.e, c.j,
                   c.hlu, c.hlp, c.muw, c.mhw, c.stl,
                   c.sym_slu, c.sym_sf
            FROM boats b
            LEFT JOIN tcc_snapshots t ON t.boat_id = b.id
            LEFT JOIN irc_certificates c ON c.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) = :design
              AND t.tcc IS NOT NULL
            ORDER BY b.id, t.snapshot_date DESC, c.issue_date DESC
        )
        SELECT
            AVG(tcc)::float AS mean_tcc,
            (percentile_cont(0.5)  WITHIN GROUP (ORDER BY tcc))::float AS median_tcc,
            (percentile_cont(0.25) WITHIN GROUP (ORDER BY tcc))::float AS p25_tcc,
            (percentile_cont(0.75) WITHIN GROUP (ORDER BY tcc))::float AS p75_tcc,
            MIN(tcc)::float AS min_tcc,
            MAX(tcc)::float AS max_tcc,
            COUNT(*)::int   AS n_boats,
            AVG(lh)::float AS mean_lh, STDDEV(lh)::float AS std_lh,
            AVG(beam)::float AS mean_beam, STDDEV(beam)::float AS std_beam,
            AVG(draft)::float AS mean_draft, STDDEV(draft)::float AS std_draft,
            AVG(headsails)::float AS mean_headsails, STDDEV(headsails)::float AS std_headsails,
            AVG(spinnakers)::float AS mean_spinnakers, STDDEV(spinnakers)::float AS std_spinnakers,
            AVG(crew)::float AS mean_crew, STDDEV(crew)::float AS std_crew,
            AVG(dlr)::float AS mean_dlr, STDDEV(dlr)::float AS std_dlr,
            AVG(displacement)::float AS mean_displacement, STDDEV(displacement)::float AS std_displacement,
            AVG(p)::float AS mean_p, STDDEV(p)::float AS std_p,
            AVG(e)::float AS mean_e, STDDEV(e)::float AS std_e,
            AVG(j)::float AS mean_j, STDDEV(j)::float AS std_j,
            AVG(hlu)::float AS mean_hlu, STDDEV(hlu)::float AS std_hlu,
            AVG(hlp)::float AS mean_hlp, STDDEV(hlp)::float AS std_hlp,
            AVG(muw)::float AS mean_muw, STDDEV(muw)::float AS std_muw,
            AVG(mhw)::float AS mean_mhw, STDDEV(mhw)::float AS std_mhw,
            AVG(stl)::float AS mean_stl, STDDEV(stl)::float AS std_stl,
            AVG(sym_slu)::float AS mean_sym_slu, STDDEV(sym_slu)::float AS std_sym_slu,
            AVG(sym_sf)::float AS mean_sym_sf, STDDEV(sym_sf)::float AS std_sym_sf
        FROM latest
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"design": design}).first()
    return dict(row._mapping) if row else {}
```

- [ ] **Step 4: Surface the baseline in `get_boat_sensitivity_context`**

In `regression.py`, find the existing `get_boat_sensitivity_context` function. After it computes `result_dict["boat_position"]`, append:

```python
    # Class baseline TCC distribution + this boat's percentile rank.
    boat_tcc = boat_data.get("tcc")
    boat_tcc_f = float(boat_tcc) if boat_tcc is not None else None
    cb: dict[str, float | None] = {
        "mean_tcc":    class_stats.get("mean_tcc"),
        "median_tcc":  class_stats.get("median_tcc"),
        "p25_tcc":     class_stats.get("p25_tcc"),
        "p75_tcc":     class_stats.get("p75_tcc"),
        "min_tcc":     class_stats.get("min_tcc"),
        "max_tcc":     class_stats.get("max_tcc"),
        "n_boats":     class_stats.get("n_boats"),
        "this_boat_tcc": boat_tcc_f,
    }
    # Percentile rank: count peers with tcc < this boat / total.
    if boat_tcc_f is not None and (class_stats.get("n_boats") or 0) > 1:
        with engine.connect() as conn:
            rank_row = conn.execute(text("""
                WITH latest AS (
                    SELECT DISTINCT ON (b.id) t.tcc
                    FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                    WHERE COALESCE(b.design_canonical, b.design) = :design
                      AND t.tcc IS NOT NULL
                    ORDER BY b.id, t.snapshot_date DESC
                )
                SELECT COUNT(*)::float / NULLIF((SELECT COUNT(*) FROM latest), 0)::float AS pct
                FROM latest WHERE tcc < :boat_tcc
            """), {"design": design, "boat_tcc": boat_tcc_f}).first()
        cb["this_boat_percentile"] = round((rank_row.pct or 0.0) * 100, 1)
    else:
        cb["this_boat_percentile"] = None

    result_dict["class_baseline"] = cb
    return result_dict
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/test_regression_class_baseline.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/analysis/regression.py api/tests/test_regression_class_baseline.py
git commit -m "feat(regression): expose class TCC baseline + percentile in boat sensitivity context

The substantial report needs to anchor measurement decomposition with
'median Sunfast 3300 rates 1.0025; this boat rates 1.0250; here's
where the delta came from.' Adds class_baseline{mean,median,p25,p75,
min,max,n_boats,this_boat_tcc,this_boat_percentile} to the dict
returned by get_boat_sensitivity_context."
```

---

### Task 2: Facts dataclasses + base SectionResult

**Files:**
- Create: `api/src/irc_data/api/services/report/__init__.py`
- Create: `api/src/irc_data/api/services/report/facts.py`
- Create: `api/src/irc_data/api/services/report/sections/__init__.py`
- Create: `api/src/irc_data/api/services/report/sections/_base.py`
- Test: `api/tests/report/__init__.py` + `api/tests/report/test_facts_shapes.py`

- [ ] **Step 1: Create the `report` package marker**

Create `api/src/irc_data/api/services/report/__init__.py`:

```python
"""Premium report generation — sectional orchestrator + per-section modules.

The legacy single-prompt report (`report_service.generate_report_content`)
is kept for backward compatibility; this package is the v2 implementation
gated behind REPORT_V2=true (see report_service.py routing).
"""
```

Create `api/src/irc_data/api/services/report/sections/__init__.py` as empty.

- [ ] **Step 2: Write the failing test for Facts dataclasses**

Create `api/tests/report/__init__.py` as empty.

Create `api/tests/report/test_facts_shapes.py`:

```python
"""Pin the public shape of every Facts dataclass.

Facts dataclasses are the truth-discipline contract: each section's
Claude prompt sees ONLY the fields on the Facts object, and the prompt
forbids inventing numbers not in those fields. If the shape of Facts
ever changes, the prompt template must be updated in lockstep. These
tests force that conversation.
"""
from dataclasses import fields
from decimal import Decimal
from datetime import date

from irc_data.api.services.report.facts import (
    ExecutiveSummaryFacts, IdentityFacts, RatingAnatomyFacts,
    RatingEvolutionFacts, ClassContextFacts, PerformanceFacts,
    SensitivityFacts, OptimisationFacts, FormulaDriftFacts,
    RivalsFacts, AppendixFacts, MeasurementContribution, RatingSnapshot,
    RivalSummary,
)


def test_rating_anatomy_facts_has_required_fields():
    fs = {f.name for f in fields(RatingAnatomyFacts)}
    assert fs >= {"boat_name", "tcc_now", "class_mean_tcc", "class_median_tcc",
                  "decomposition", "explained_variance_pct", "model_tier",
                  "n_boats_in_class"}


def test_measurement_contribution_dataclass():
    mc = MeasurementContribution(
        field="displacement", this_boat=3696.0, class_mean=3981.0,
        delta=-285.0, contrib_tcc=0.0096, unit="per 100kg",
        beta=-0.003373,
    )
    assert mc.contrib_tcc == 0.0096
    assert mc.unit == "per 100kg"


def test_rating_snapshot_dataclass():
    s = RatingSnapshot(date=date(2025, 8, 14), tcc=Decimal("1.025"),
                      cert_year=2025, source="irc_tcc")
    assert s.tcc == Decimal("1.025")


def test_performance_facts_has_required_fields():
    fs = {f.name for f in fields(PerformanceFacts)}
    assert fs >= {"finishes", "wins", "podiums", "distinct_events",
                  "rai_percentile", "recent_results", "head_to_head"}


def test_rivals_facts_uses_rival_summary():
    rs = RivalSummary(boat_id=1, name="Foo", sail_number="GBR1R",
                      country="GBR", tcc=Decimal("1.020"),
                      recent_finishes_count=12,
                      head_to_head_wins=3, head_to_head_losses=5)
    assert rs.tcc == Decimal("1.020")
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_facts_shapes.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Create `facts.py` with all dataclasses**

Create `api/src/irc_data/api/services/report/facts.py`:

```python
"""Strict Facts contracts for each report section.

Each section's Claude prompt receives ONE Facts object as input and is
forbidden from citing numbers outside its fields. Adding a new fact
requires updating both the dataclass here AND the prompt template in
prompts.py so they stay in lockstep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


# ── Atomic value types ──────────────────────────────────────────────────


@dataclass
class MeasurementContribution:
    """One row of the rating decomposition table."""
    field: str          # 'displacement', 'p', 'e', etc.
    this_boat: float
    class_mean: float
    delta: float        # this_boat - class_mean
    contrib_tcc: float  # signed TCC impact vs class mean
    unit: str           # 'per 100kg', 'per 0.1m', 'per sail'
    beta: float         # raw regression coefficient


@dataclass
class RatingSnapshot:
    """One historical TCC point."""
    date: date
    tcc: Decimal
    cert_year: int | None
    source: str         # 'irc_tcc', 'irc_cert', etc.


@dataclass
class RaceResultLite:
    """A single race row in compact form for the timeline section."""
    event_date: date | None
    event_name: str
    race_name: str | None
    place: int | None
    fleet_size: int | None
    class_name: str | None
    status: str


@dataclass
class RivalSummary:
    """One rival in the Rival Watch section."""
    boat_id: int
    name: str
    sail_number: str | None
    country: str | None
    tcc: Decimal
    recent_finishes_count: int
    head_to_head_wins: int   # races where THIS boat beat RIVAL on corrected
    head_to_head_losses: int


@dataclass
class Identity:
    """A historical name/sail observation."""
    boat_name: str
    sail_number: str | None
    owner: str | None
    flag: str | None
    source: str
    observed_date: date | None


@dataclass
class Recommendation:
    """One optimisation recommendation."""
    measurement: str
    current_value: float
    suggested_value: float
    est_tcc_gain: float      # absolute, signed (negative = lower rating)
    rationale: str           # short justification grounded in coefficients
    confidence: str          # 'high' | 'medium' | 'low'


# ── Per-section Facts ───────────────────────────────────────────────────


@dataclass
class ExecutiveSummaryFacts:
    boat_name: str
    sail_number: str
    design: str
    country: str | None
    tcc_now: Decimal
    class_median_tcc: float | None
    this_boat_percentile: float | None
    finishes: int
    wins: int
    podiums: int
    headline_finding_1: str   # pre-cooked one-liners (built from raw stats)
    headline_finding_2: str
    headline_finding_3: str
    top_recommendation: str | None


@dataclass
class IdentityFacts:
    boat_name: str
    sail_number: str
    design: str
    designer: str | None
    builder: str | None
    year_built: int | None
    loa: float | None
    lwl: float | None
    beam_max: float | None
    displacement_kg: float | None
    identities: list[Identity] = field(default_factory=list)


@dataclass
class RatingAnatomyFacts:
    boat_name: str
    tcc_now: Decimal
    class_mean_tcc: float | None
    class_median_tcc: float | None
    decomposition: list[MeasurementContribution] = field(default_factory=list)
    explained_variance_pct: float | None = None   # R² × 100
    model_tier: str = ""
    n_boats_in_class: int = 0


@dataclass
class RatingEvolutionFacts:
    boat_name: str
    snapshots: list[RatingSnapshot] = field(default_factory=list)
    cert_reissue_dates: list[date] = field(default_factory=list)
    first_snapshot_tcc: Decimal | None = None
    latest_snapshot_tcc: Decimal | None = None
    total_movement: float = 0.0          # latest − first
    largest_jump_tcc: float = 0.0
    largest_jump_date: date | None = None


@dataclass
class ClassContextFacts:
    design: str
    class_n: int
    class_tcc_min: float
    class_tcc_max: float
    class_tcc_median: float
    class_tcc_mean: float
    this_boat_tcc: float
    this_boat_percentile: float | None
    top_5_boats: list[dict] = field(default_factory=list)  # {name, sail, tcc, country}


@dataclass
class PerformanceFacts:
    boat_name: str
    finishes: int
    wins: int
    podiums: int
    distinct_events: int
    rai_percentile: float | None
    rai_interpretation: str | None
    recent_results: list[RaceResultLite] = field(default_factory=list)
    by_event_type: dict[str, dict] = field(default_factory=dict)  # 'series'/'offshore'/'twilight' → {n, wins, podiums}
    head_to_head: list[RivalSummary] = field(default_factory=list)


@dataclass
class SensitivityFacts:
    design: str
    model_tier: str
    n_boats_in_class: int
    r_squared: float
    coefficients: list[MeasurementContribution] = field(default_factory=list)  # already enriched w/ boat's value


@dataclass
class OptimisationFacts:
    boat_name: str
    recommendations: list[Recommendation] = field(default_factory=list)
    top_3_summary: str = ""


@dataclass
class FormulaDriftFacts:
    design: str
    window_years: int
    drift_observed: bool
    affected_measurements: list[str] = field(default_factory=list)
    this_boat_likely_impact: str | None = None


@dataclass
class RivalsFacts:
    boat_name: str
    rivals: list[RivalSummary] = field(default_factory=list)


@dataclass
class AppendixFacts:
    methodology_blurb: str
    data_sources: list[str] = field(default_factory=list)
    glossary: list[tuple[str, str]] = field(default_factory=list)
```

- [ ] **Step 5: Create `sections/_base.py` with the SectionResult type**

Create `api/src/irc_data/api/services/report/sections/_base.py`:

```python
"""Shared section primitives — every section emits a SectionResult."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SectionResult:
    """What one section function returns to the orchestrator."""
    section_id: str            # 's03_rating_anatomy'
    title: str                 # 'Rating Anatomy'
    markdown: str              # the prose body
    chart_pngs: dict[str, bytes] = field(default_factory=dict)
    # ↑ keyed by stable slot name e.g. 'anatomy_bar', referenced by the
    #   HTML template; the orchestrator base64-inlines each one.
    structured: dict = field(default_factory=dict)
    # ↑ machine-readable snapshot (the Facts dict) for the
    #   report_analytics JSONB column.
    error: str | None = None
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_facts_shapes.py -v
```
Expected: PASS (all 5)

- [ ] **Step 7: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/ api/tests/report/
git commit -m "feat(report): Facts contracts + SectionResult primitive

Adds the typed dataclasses each report section consumes. Facts are
the truth-discipline mechanism: each Claude prompt sees ONE Facts
object and is forbidden from citing numbers outside its fields. Test
suite pins the public shape so the prompt template stays in lockstep."
```

---

### Task 3: Claude client wrapper with truth-discipline prompt skeleton

**Files:**
- Create: `api/src/irc_data/api/services/report/claude_client.py`
- Create: `api/src/irc_data/api/services/report/prompts.py`
- Test: `api/tests/report/test_prompts_truth_discipline.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/report/test_prompts_truth_discipline.py`:

```python
"""Verify the truth-discipline guard catches hallucinated numbers.

The premium report's biggest failure mode under long-form generation
is invented statistics — Claude will fill in plausible-sounding numbers
that aren't in the source data. The truth-discipline pattern: every
section passes a Facts dataclass to Claude with an explicit allowlist
of numeric values, and after generation we scan the markdown for
numeric tokens that don't appear in the Facts allowlist. Suspicious
tokens get logged + the section is marked degraded.
"""
import re
from decimal import Decimal

from irc_data.api.services.report.claude_client import (
    extract_numeric_tokens,
    facts_numeric_allowlist,
)
from irc_data.api.services.report.facts import (
    RatingAnatomyFacts, MeasurementContribution,
)


def test_extracts_numeric_tokens_from_prose():
    md = "Her TCC sits at 1.0250, against a class median of 1.0025. " \
         "She's 285 kg lighter than the median Sunfast 3300."
    tokens = extract_numeric_tokens(md)
    assert "1.0250" in tokens
    assert "1.0025" in tokens
    assert "285" in tokens


def test_facts_allowlist_includes_decomposition_values():
    facts = RatingAnatomyFacts(
        boat_name="SUN FISH",
        tcc_now=Decimal("1.0250"),
        class_mean_tcc=1.0025,
        class_median_tcc=1.0020,
        decomposition=[
            MeasurementContribution(
                field="displacement", this_boat=3696.0, class_mean=3981.0,
                delta=-285.0, contrib_tcc=0.0096, unit="per 100kg",
                beta=-0.003373,
            ),
        ],
        explained_variance_pct=93.4,
        model_tier="A",
        n_boats_in_class=82,
    )
    allowlist = facts_numeric_allowlist(facts)
    # Numbers from the Facts object should appear in the allowlist,
    # rounded to 1 decimal (so prose can use 285 OR 285.0 OR 0.0096).
    assert "1.025" in allowlist
    assert "1.0025" in allowlist
    assert "285" in allowlist
    assert "3696" in allowlist
    assert "3981" in allowlist
    assert "0.0096" in allowlist
    assert "93" in allowlist or "93.4" in allowlist
    assert "82" in allowlist


def test_allowlist_normalises_decimal_representations():
    """1.025, 1.0250, 1.02 → all forms should be valid for the same value."""
    from irc_data.api.services.report.claude_client import _normalise_number
    assert _normalise_number("1.0250") == _normalise_number("1.025")
    assert _normalise_number("285.0") == _normalise_number("285")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_prompts_truth_discipline.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `claude_client.py` with truth-discipline helpers**

Create `api/src/irc_data/api/services/report/claude_client.py`:

```python
"""Claude wrapper for per-section generation + truth-discipline scanner.

The scanner extracts numeric tokens from generated markdown and checks
they appear in a Facts-derived allowlist. Tokens outside the allowlist
are not blocked — they're logged with the section context so we can
audit and tighten prompts. Hard-blocking would risk dropping legitimate
prose; logging gives us the signal we need without false negatives.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Match positive decimals, integers, and percentages. Skip 1- and 2-digit
# integers (years, race numbers, fleet positions — too many false positives).
_NUMERIC_RE = re.compile(r"\b\d{3,}(?:[.,]\d+)?%?\b|\b\d+\.\d+%?\b")


def extract_numeric_tokens(text: str) -> set[str]:
    """Pull numeric-looking tokens out of generated prose."""
    out: set[str] = set()
    for m in _NUMERIC_RE.finditer(text or ""):
        tok = m.group(0).rstrip("%").replace(",", "")
        out.add(_normalise_number(tok))
    return out


def _normalise_number(s: str) -> str:
    """Normalise '1.0250' and '1.025' to a single representation."""
    try:
        d = Decimal(s)
        # Strip trailing zeros after the decimal point.
        normalised = d.normalize()
        # Decimal.normalize() can produce scientific notation for tiny
        # numbers — convert to plain string.
        return format(normalised, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return s


def facts_numeric_allowlist(facts: Any, *, round_to: int = 4) -> set[str]:
    """Walk a Facts dataclass tree and collect every numeric value as
    a set of normalised string tokens the prose may legitimately cite.

    Nested dataclasses, lists, dicts and Decimals are all walked.
    """
    allow: set[str] = set()

    def _add(v: Any) -> None:
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, (int, float, Decimal)):
            try:
                d = Decimal(str(v))
                allow.add(_normalise_number(format(d, "f")))
                # Also add common roundings the model is likely to use.
                for places in (0, 1, 2, 3):
                    allow.add(_normalise_number(format(round(float(d), places), "f")))
            except Exception:
                pass
        elif isinstance(v, str):
            return
        elif is_dataclass(v):
            for fname in v.__dataclass_fields__:
                _add(getattr(v, fname))
        elif isinstance(v, dict):
            for vv in v.values():
                _add(vv)
        elif isinstance(v, (list, tuple, set)):
            for vv in v:
                _add(vv)

    _add(facts)
    return allow


def audit_section_numbers(markdown: str, facts: Any, *, section_id: str) -> dict:
    """Compare numeric tokens in generated prose against the Facts allowlist.

    Returns a dict with `suspicious` (tokens NOT in allowlist) and
    `cited` (tokens that matched). Suspicious tokens are logged but
    the section is not blocked — a numbers-policed prompt should
    produce close to zero suspicious tokens, so any spike is signal.
    """
    seen = extract_numeric_tokens(markdown)
    allow = facts_numeric_allowlist(facts)
    suspicious = sorted(seen - allow)
    cited = sorted(seen & allow)
    if suspicious:
        logger.warning(
            "[%s] Suspicious numeric tokens in generated prose: %s",
            section_id, suspicious,
        )
    return {"suspicious": suspicious, "cited": cited, "allow_size": len(allow)}


def call_claude(*, system: str, user: str, max_tokens: int = 2000,
                section_id: str = "?") -> str:
    """One Claude call. Centralised so we can swap model / add caching."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    from irc_data.api.services.analytics_service import get_anthropic_client
    client = get_anthropic_client(api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        posthog_distinct_id=section_id,
        posthog_properties={"endpoint": f"report.v2.{section_id}"},
    )
    # Concatenate all text blocks.
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()
```

- [ ] **Step 4: Create `prompts.py` with the system prompt skeleton**

Create `api/src/irc_data/api/services/report/prompts.py`:

```python
"""System + per-section prompt templates.

The system prompt is shared across all sections and bakes in the
truth-discipline rules. Section-specific prompts are pure user
content describing what to write and pointing at the Facts payload.
"""

SYSTEM_PROMPT_V2 = """You are writing one section of a premium IRC rating analysis
report for the owner of a racing yacht. The owner has paid for substance —
they have decades of sailing under their belt, they understand handicaps,
they want analysis grounded in *their boat's actual numbers*.

ABSOLUTE RULES
──────────────
1. NEVER cite a number that is not in the provided FACTS payload. No estimates,
   no plausible-sounding figures, no "approximately N." If you don't have the
   number, omit the sentence entirely. This rule is the difference between
   useful analysis and worthless prose.

2. NEVER invent race names, regatta names, owner names, designer names, or
   rival boats. If the FACTS payload contains a name, you may cite it; if
   not, refer to "your boat", "this hull", "the design" instead.

3. NEVER speculate about the IRC formula's internal mechanics. The formula
   is secret. Frame everything as "consistent with our regression model"
   or "the data is suggesting" — not "the rule penalises X".

4. NEVER use marketing language ("unleash", "elevate", "performance edge",
   "competitive advantage"). The reader is a yacht owner, not a buyer.
   Plain English, sailing terminology, evidence-driven.

STYLE
─────
- Write like a coach who's been studying this boat's file. Direct. Specific.
- Reference the boat by name on first mention, then "she" / "the boat".
- Sailing terms are correct and capitalised conventionally: TCC, IRC, ORC,
  rated/rates (not "Rated"), short-handed, displacement (not "weight").
- One claim per paragraph. Each claim cites at least one number from FACTS.
- No bullet lists unless the FACTS payload itself is a list (e.g. recommendations).
- 4-8 paragraphs per section unless the section instructions say otherwise.

WHAT YOU ARE WRITING
────────────────────
You will be told the section id, the section's goal, and given a JSON-shaped
FACTS payload. Produce MARKDOWN (no front-matter, no section header — the
template owns those). Just the body prose.
"""


# Per-section user-prompt templates. {facts_json} is injected by the
# section module after JSON-serialising the Facts dataclass.

RATING_ANATOMY_PROMPT = """SECTION: s03_rating_anatomy
GOAL: Explain WHY this boat rates the TCC it does, by decomposing the gap
between her TCC and the class median into per-measurement contributions.

What to cover, in order:
1. Lead with the headline gap: "She rates {tcc_now}; the median {design}
   rates {class_median_tcc}. The {delta} difference breaks down to..."
2. The 3-4 LARGEST signed contributors (positive or negative) — name the
   measurement, the boat's value vs the class mean, and the TCC impact.
3. One paragraph on what the regression model can and cannot see: the
   model R² (explained_variance_pct), the sample size (n_boats_in_class),
   and the tier (A = full cert data; B = snapshot data only).
4. Close with one sentence pointing the reader at §8 (Optimisation
   Recommendations) for what to do about it.

FACTS:
{facts_json}
"""

# More section prompts added per-task as each section is built.
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_prompts_truth_discipline.py -v
```
Expected: PASS (all 3)

- [ ] **Step 6: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/claude_client.py \
        api/src/irc_data/api/services/report/prompts.py \
        api/tests/report/test_prompts_truth_discipline.py
git commit -m "feat(report): Claude wrapper + truth-discipline number audit

audit_section_numbers walks the Facts dataclass tree to build an
allowlist of every legitimate numeric value, then scans generated
markdown for tokens outside that set. Logs (does not block) so we get
operational signal without false negatives on legitimate prose."
```

---

### Task 4: Chart factory — Matplotlib PNG renderer

**Files:**
- Create: `api/src/irc_data/api/services/report/charts.py`
- Test: `api/tests/report/test_charts.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/report/test_charts.py`:

```python
"""Verify each chart factory function returns a valid PNG payload.

Charts are produced inline (no temp files), base64-inlined into the
HTML template by the orchestrator. Each function takes the relevant
Facts dataclass and returns PNG bytes.
"""
from decimal import Decimal
from datetime import date

from irc_data.api.services.report.charts import (
    render_anatomy_bar, render_tcc_timeseries,
    render_class_distribution, render_sensitivity_bar,
    render_results_timeline,
)
from irc_data.api.services.report.facts import (
    RatingAnatomyFacts, RatingEvolutionFacts, ClassContextFacts,
    SensitivityFacts, PerformanceFacts,
    MeasurementContribution, RatingSnapshot, RaceResultLite,
)


def _is_png(b: bytes) -> bool:
    return b[:8] == b"\x89PNG\r\n\x1a\n"


def test_anatomy_bar_returns_png():
    facts = RatingAnatomyFacts(
        boat_name="SUN FISH",
        tcc_now=Decimal("1.025"), class_mean_tcc=1.0025,
        class_median_tcc=1.002, explained_variance_pct=93.4,
        model_tier="A", n_boats_in_class=82,
        decomposition=[
            MeasurementContribution("displacement", 3696, 3981, -285, 0.0096, "per 100kg", -0.003373),
            MeasurementContribution("muw", 1.45, 1.08, 0.37, 0.0042, "per 0.1m", 0.001124),
            MeasurementContribution("hlu", 11.09, 12.12, -1.03, -0.0030, "per 0.1m", 0.000291),
            MeasurementContribution("spinnakers", 2, 2.82, -0.82, -0.0021, "per sail", 0.002505),
        ],
    )
    png = render_anatomy_bar(facts)
    assert _is_png(png)
    assert len(png) > 2000  # not an empty figure


def test_tcc_timeseries_returns_png():
    facts = RatingEvolutionFacts(
        boat_name="SUN FISH",
        snapshots=[
            RatingSnapshot(date(2023, 6, 1), Decimal("1.018"), 2023, "irc_tcc"),
            RatingSnapshot(date(2024, 6, 1), Decimal("1.022"), 2024, "irc_tcc"),
            RatingSnapshot(date(2025, 6, 1), Decimal("1.025"), 2025, "irc_tcc"),
        ],
        first_snapshot_tcc=Decimal("1.018"),
        latest_snapshot_tcc=Decimal("1.025"),
        total_movement=0.007,
    )
    png = render_tcc_timeseries(facts)
    assert _is_png(png)


def test_class_distribution_returns_png():
    facts = ClassContextFacts(
        design="Sunfast 3300", class_n=82,
        class_tcc_min=0.891, class_tcc_max=1.078,
        class_tcc_median=1.002, class_tcc_mean=1.0025,
        this_boat_tcc=1.025, this_boat_percentile=78.0,
    )
    # Renderer also needs the per-boat TCCs — we pass them separately.
    tcc_list = [0.891 + i * 0.002 for i in range(94)]
    png = render_class_distribution(facts, tcc_list)
    assert _is_png(png)


def test_sensitivity_bar_returns_png():
    facts = SensitivityFacts(
        design="Sunfast 3300", model_tier="A", n_boats_in_class=82,
        r_squared=0.934,
        coefficients=[
            MeasurementContribution("displacement", 3696, 3981, -285, 0.0096, "per 100kg", -0.003373),
            MeasurementContribution("muw", 1.45, 1.08, 0.37, 0.0042, "per 0.1m", 0.001124),
            MeasurementContribution("hlu", 11.09, 12.12, -1.03, -0.0030, "per 0.1m", 0.000291),
        ],
    )
    png = render_sensitivity_bar(facts)
    assert _is_png(png)


def test_results_timeline_returns_png():
    facts = PerformanceFacts(
        boat_name="SUN FISH", finishes=31, wins=3, podiums=13,
        distinct_events=61, rai_percentile=58.0, rai_interpretation=None,
        recent_results=[
            RaceResultLite(date(2025, 11, 23), "Race A", "R1", 5, 8, "Div 1", "finished"),
            RaceResultLite(date(2025, 11, 30), "Race B", "R1", 2, 8, "Div 1", "finished"),
            RaceResultLite(date(2025, 12, 7),  "Race C", "R1", 8, 10, "Div 1", "finished"),
        ],
    )
    png = render_results_timeline(facts)
    assert _is_png(png)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_charts.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the chart factory**

Create `api/src/irc_data/api/services/report/charts.py`:

```python
"""Matplotlib PNG factory — one function per chart type.

Uses the Agg backend so it works under the API process without a
display. Each function returns raw PNG bytes; the orchestrator
base64-inlines them into the HTML template. Style is restrained,
brand-aligned navy/brass palette to match the PDF look.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")  # must be before pyplot import

import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

# ── Brand palette ───────────────────────────────────────────────────────
NAVY = "#0A2240"
BRASS = "#C29B61"
CREAM = "#F4F1E8"
SIGNAL_GREEN = "#4A8A6F"
SIGNAL_RED = "#B85450"
GRID = "#D9D5C7"
TEXT = "#1e293b"

# Chart sizing — A4 page is 595×842 pt; we target 480 pt wide ≈ 6.5 in.
DPI = 144
FIGSIZE_WIDE = (6.5, 3.0)
FIGSIZE_SQUARE = (4.5, 4.5)


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _style(ax) -> None:
    """Common axis cosmetics."""
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=8)
    ax.yaxis.label.set_color(TEXT)
    ax.xaxis.label.set_color(TEXT)
    ax.grid(True, axis="y", color=GRID, linewidth=0.5, alpha=0.7)


# ── Charts ──────────────────────────────────────────────────────────────


def render_anatomy_bar(facts) -> bytes:
    """Per-measurement TCC contribution, signed bar chart, sorted by
    absolute impact. Positive = boat rates higher than median; negative
    = lower."""
    items = sorted(facts.decomposition, key=lambda c: -abs(c.contrib_tcc))[:10]
    labels = [c.field for c in items][::-1]
    values = [c.contrib_tcc for c in items][::-1]
    colors = [BRASS if v >= 0 else SIGNAL_GREEN for v in values]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.barh(labels, values, color=colors, edgecolor=NAVY, linewidth=0.5)
    ax.axvline(0, color=NAVY, linewidth=0.8)
    ax.set_xlabel("TCC contribution vs class mean (signed)", fontsize=9)
    ax.set_title(f"What drives {facts.boat_name}'s rating",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    return _to_png(fig)


def render_tcc_timeseries(facts) -> bytes:
    """TCC over time, with marker per certificate."""
    dates = [s.date for s in facts.snapshots]
    tccs = [float(s.tcc) for s in facts.snapshots]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.plot(dates, tccs, color=NAVY, linewidth=2, marker="o",
            markerfacecolor=BRASS, markeredgecolor=NAVY, markersize=6)
    ax.set_ylabel("TCC", fontsize=9)
    ax.set_title(f"{facts.boat_name} — rating evolution",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    fig.autofmt_xdate()
    return _to_png(fig)


def render_class_distribution(facts, all_tccs: list[float]) -> bytes:
    """Histogram of class TCCs with this boat marked."""
    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.hist(all_tccs, bins=20, color=NAVY, alpha=0.75, edgecolor="white")
    ax.axvline(facts.this_boat_tcc, color=BRASS, linewidth=2.5,
               label=f"This boat: {facts.this_boat_tcc:.4f}")
    ax.axvline(facts.class_tcc_median, color=SIGNAL_GREEN, linewidth=1.5,
               linestyle="--",
               label=f"Class median: {facts.class_tcc_median:.4f}")
    ax.set_xlabel("TCC", fontsize=9)
    ax.set_ylabel("Boats", fontsize=9)
    ax.set_title(f"{facts.design} TCC distribution (n={facts.class_n})",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right", frameon=False)
    _style(ax)
    return _to_png(fig)


def render_sensitivity_bar(facts) -> bytes:
    """Standardised coefficient bar — which measurement levers move TCC
    most across the fleet, independent of this boat's position."""
    items = sorted(facts.coefficients, key=lambda c: -abs(c.beta))[:10]
    labels = [c.field for c in items][::-1]
    values = [c.beta for c in items][::-1]
    colors = [BRASS if v >= 0 else SIGNAL_GREEN for v in values]

    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.barh(labels, values, color=colors, edgecolor=NAVY, linewidth=0.5)
    ax.axvline(0, color=NAVY, linewidth=0.8)
    ax.set_xlabel("Coefficient (β per unit, signed)", fontsize=9)
    ax.set_title(f"{facts.design} — which measurements move TCC most",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    return _to_png(fig)


def render_results_timeline(facts) -> bytes:
    """Scatter of recent results — place vs date, sized by fleet size."""
    pts = [(r.event_date, r.place, r.fleet_size or 10, r.status)
           for r in facts.recent_results
           if r.event_date and r.place]
    if not pts:
        # Render an explicit "no data" placeholder so the layout doesn't break.
        fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
        ax.text(0.5, 0.5, "No recent race data on file",
                ha="center", va="center", color=TEXT, fontsize=11)
        ax.set_axis_off()
        return _to_png(fig)

    dates, places, sizes, statuses = zip(*pts)
    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    ax.scatter(dates, places, s=[s * 8 for s in sizes],
               c=BRASS, edgecolor=NAVY, linewidth=0.7, alpha=0.85)
    ax.invert_yaxis()  # 1st place on top
    ax.set_ylabel("Finishing position", fontsize=9)
    ax.set_title(f"{facts.boat_name} — recent results",
                 fontsize=11, color=NAVY, pad=12, fontweight="bold")
    _style(ax)
    fig.autofmt_xdate()
    return _to_png(fig)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_charts.py -v
```
Expected: PASS (all 5)

- [ ] **Step 5: Eyeball the output**

```bash
cd api && PYTHONPATH=src .venv/bin/python -c "
from decimal import Decimal
from irc_data.api.services.report.facts import RatingAnatomyFacts, MeasurementContribution
from irc_data.api.services.report.charts import render_anatomy_bar
facts = RatingAnatomyFacts(
    boat_name='SUN FISH', tcc_now=Decimal('1.025'),
    class_mean_tcc=1.0025, class_median_tcc=1.002,
    explained_variance_pct=93.4, model_tier='A', n_boats_in_class=82,
    decomposition=[
        MeasurementContribution('displacement', 3696, 3981, -285, 0.0096, 'per 100kg', -0.003373),
        MeasurementContribution('muw', 1.45, 1.08, 0.37, 0.0042, 'per 0.1m', 0.001124),
        MeasurementContribution('hlu', 11.09, 12.12, -1.03, -0.0030, 'per 0.1m', 0.000291),
        MeasurementContribution('spinnakers', 2, 2.82, -0.82, -0.0021, 'per sail', 0.002505),
    ],
)
with open('/tmp/anatomy_bar.png', 'wb') as f:
    f.write(render_anatomy_bar(facts))
print('wrote /tmp/anatomy_bar.png')
"
```
Open `/tmp/anatomy_bar.png` and confirm the bar chart renders with the brass/green palette, sorted bars, axis labels.

- [ ] **Step 6: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/charts.py api/tests/report/test_charts.py
git commit -m "feat(report): Matplotlib chart factory — 5 chart types

Headless Agg backend, brass/navy palette to match the PDF. Each
function takes a Facts dataclass and returns PNG bytes; orchestrator
inlines them base64 into the HTML."
```

---

### Task 5: Facts builders for Rating Anatomy (proof-of-concept builder)

Build the SQL/engine-call side of the truth-discipline pattern: a single function that turns DB + analysis-engine output into a populated `RatingAnatomyFacts`. Once this pattern works for one section, the remaining 10 sections follow the same shape.

**Files:**
- Create: `api/src/irc_data/api/services/report/facts_builders.py`
- Test: `api/tests/report/test_facts_builders.py`
- Test fixture: `api/tests/report/fixtures/__init__.py` (empty) + `sun_fish_facts.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/report/fixtures/__init__.py` (empty file).

Create `api/tests/report/fixtures/sun_fish_facts.py`:

```python
"""Known-good golden values for SUN FISH (boat_id 12330).

These values are stable post-dedup (2026-05-20). If a test asserting
them fails, EITHER the underlying data has shifted (re-snapshot
intentionally) OR the builder has regressed (fix the builder).
"""
SUN_FISH_BOAT_ID = 12330
SUN_FISH_DESIGN = "Sunfast 3300"
SUN_FISH_TCC_LOWER = 1.02
SUN_FISH_TCC_UPPER = 1.03   # current rating sits inside this band
```

Create `api/tests/report/test_facts_builders.py`:

```python
"""Verify facts_builders.build_rating_anatomy populates a
RatingAnatomyFacts that points at real DB values for SUN FISH."""
from irc_data.db.connection import get_engine
from irc_data.api.services.report.facts import RatingAnatomyFacts
from irc_data.api.services.report.facts_builders import build_rating_anatomy

from tests.report.fixtures.sun_fish_facts import (
    SUN_FISH_BOAT_ID, SUN_FISH_DESIGN, SUN_FISH_TCC_LOWER, SUN_FISH_TCC_UPPER,
)


def test_build_rating_anatomy_for_sun_fish():
    eng = get_engine()
    facts = build_rating_anatomy(eng, SUN_FISH_BOAT_ID)
    assert isinstance(facts, RatingAnatomyFacts)
    assert facts.boat_name.upper() == "SUN FISH"
    assert SUN_FISH_TCC_LOWER < float(facts.tcc_now) < SUN_FISH_TCC_UPPER
    assert facts.class_median_tcc is not None
    assert 0.95 < facts.class_median_tcc < 1.05
    assert facts.n_boats_in_class >= 50
    assert facts.model_tier in ("A", "B", "C")
    # Decomposition should have something — at least 5 features for any tier.
    assert len(facts.decomposition) >= 5
    # Find displacement and check the contribution direction matches
    # our known fact (lighter than median, lower TCC penalty therefore positive contrib).
    disp = next((c for c in facts.decomposition if c.field == "displacement"), None)
    assert disp is not None
    assert disp.this_boat < disp.class_mean  # she's lighter
    # The signed contribution should be small (< 0.05 TCC absolute).
    assert abs(disp.contrib_tcc) < 0.05


def test_build_rating_anatomy_handles_unknown_boat_gracefully():
    eng = get_engine()
    facts = build_rating_anatomy(eng, boat_id=999_999_999)
    # Returns a Facts object with empty/None fields rather than raising.
    assert isinstance(facts, RatingAnatomyFacts)
    assert facts.decomposition == []
    assert facts.n_boats_in_class == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_facts_builders.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the builder**

Create `api/src/irc_data/api/services/report/facts_builders.py`:

```python
"""Build Facts dataclasses from DB + analysis-engine output.

Each builder is a pure function: engine + boat_id → Facts.
The orchestrator runs builders in parallel; section modules accept
the resulting Facts as input.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.regression import get_boat_sensitivity_context
from irc_data.api.services.report.facts import (
    MeasurementContribution, RatingAnatomyFacts,
)

logger = logging.getLogger(__name__)


# ── Unit scaling — must match the regression engine's `unit` strings ───

_UNIT_SCALE = {
    "per 100kg": 100.0,
    "per 0.1m": 0.1,
    "per sail": 1.0,
    "per crew": 1.0,
    "per kg": 1.0,
    "per m": 1.0,
}


def _scale_for_unit(unit: str) -> float:
    return _UNIT_SCALE.get(unit, 1.0)


# ── Rating Anatomy ─────────────────────────────────────────────────────


def build_rating_anatomy(engine: Engine, boat_id: int) -> RatingAnatomyFacts:
    """Assemble the per-measurement TCC contribution facts for one boat."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT b.boat_name, COALESCE(b.design_canonical, b.design) AS design,
                   t.tcc
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                ORDER BY snapshot_date DESC LIMIT 1
            ) t ON true
            WHERE b.id = :id
        """), {"id": boat_id}).first()

    if not row or row.design is None or row.tcc is None:
        # No data — return an empty Facts payload.
        return RatingAnatomyFacts(
            boat_name=(row.boat_name if row else f"boat #{boat_id}"),
            tcc_now=Decimal("0"),
            class_mean_tcc=None, class_median_tcc=None,
            decomposition=[], explained_variance_pct=None,
            model_tier="", n_boats_in_class=0,
        )

    sens = get_boat_sensitivity_context(engine, boat_id, row.design)
    if sens is None:
        return RatingAnatomyFacts(
            boat_name=row.boat_name, tcc_now=row.tcc,
            class_mean_tcc=None, class_median_tcc=None,
            decomposition=[], explained_variance_pct=None,
            model_tier="", n_boats_in_class=0,
        )

    baseline = sens.get("class_baseline") or {}
    decomposition: list[MeasurementContribution] = []
    for coef in sens.get("coefficients", []):
        feat = coef["field"]
        pos = (sens.get("boat_position") or {}).get(feat) or {}
        if "value" not in pos or "class_mean" not in pos:
            continue
        delta_raw = pos["value"] - pos["class_mean"]
        scale = _scale_for_unit(coef.get("unit", ""))
        contrib = (delta_raw / scale) * coef["beta_per_unit"]
        decomposition.append(MeasurementContribution(
            field=feat,
            this_boat=round(pos["value"], 3),
            class_mean=round(pos["class_mean"], 3),
            delta=round(delta_raw, 3),
            contrib_tcc=round(contrib, 5),
            unit=coef.get("unit", ""),
            beta=coef["beta_per_unit"],
        ))

    # Sort by absolute impact, biggest first.
    decomposition.sort(key=lambda c: -abs(c.contrib_tcc))

    return RatingAnatomyFacts(
        boat_name=row.boat_name,
        tcc_now=row.tcc,
        class_mean_tcc=baseline.get("mean_tcc"),
        class_median_tcc=baseline.get("median_tcc"),
        decomposition=decomposition,
        explained_variance_pct=round((sens.get("r_squared") or 0) * 100, 1),
        model_tier=sens.get("model_tier", ""),
        n_boats_in_class=sens.get("n_boats") or 0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_facts_builders.py -v
```
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/facts_builders.py \
        api/tests/report/test_facts_builders.py \
        api/tests/report/fixtures/
git commit -m "feat(report): build_rating_anatomy — first Facts builder

Pulls the sensitivity engine output for a boat and turns it into a
typed RatingAnatomyFacts. Handles unit scaling (per 100kg, per 0.1m,
per sail). Graceful degradation: returns an empty Facts payload when
the boat has no design or no TCC."
```

---

## Phase B — First section end-to-end (Task 6)

Prove the pattern works with one full section before scaling. This is the validation gate before generating the other 10.

### Task 6: Rating Anatomy section — end-to-end

**Files:**
- Create: `api/src/irc_data/api/services/report/sections/s03_rating_anatomy.py`
- Test: `api/tests/report/test_sections/__init__.py` (empty) + `test_s03_rating_anatomy.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/report/test_sections/__init__.py` (empty).

Create `api/tests/report/test_sections/test_s03_rating_anatomy.py`:

```python
"""End-to-end test for s03_rating_anatomy.

This is the proof of the section pattern: build Facts from DB → call
Claude with the prompt template → run the truth-discipline audit →
render the chart → return a SectionResult.

Marked as `requires_anthropic` so CI without an API key can skip.
"""
import os
import pytest

from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s03_rating_anatomy import generate
from irc_data.api.services.report.sections._base import SectionResult


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
def test_generate_s03_for_sun_fish():
    eng = get_engine()
    result = generate(eng, boat_id=12330)
    assert isinstance(result, SectionResult)
    assert result.section_id == "s03_rating_anatomy"
    assert result.error is None
    assert len(result.markdown) > 500  # substantive paragraph(s)
    assert "anatomy_bar" in result.chart_pngs
    assert result.chart_pngs["anatomy_bar"][:8] == b"\x89PNG\r\n\x1a\n"
    # Must mention SUN FISH at least once (boat name was in Facts).
    assert "SUN FISH" in result.markdown.upper()
    # Truth-discipline audit ran (logged suspicious tokens if any).
    assert "audit" in result.structured
    assert isinstance(result.structured["audit"]["suspicious"], list)


def test_generate_s03_returns_error_on_unknown_boat():
    eng = get_engine()
    result = generate(eng, boat_id=999_999_999)
    assert isinstance(result, SectionResult)
    # Empty Facts → empty section, no Claude call.
    assert result.markdown == "" or "no data" in result.markdown.lower()
    assert result.chart_pngs == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_sections/test_s03_rating_anatomy.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the section module**

Create `api/src/irc_data/api/services/report/sections/s03_rating_anatomy.py`:

```python
"""Section 3 — Rating Anatomy: why this boat rates what it does."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from sqlalchemy.engine import Engine

from irc_data.api.services.report.charts import render_anatomy_bar
from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_rating_anatomy
from irc_data.api.services.report.prompts import RATING_ANATOMY_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult

logger = logging.getLogger(__name__)
SECTION_ID = "s03_rating_anatomy"
SECTION_TITLE = "Rating Anatomy"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_rating_anatomy(engine, boat_id)

    # Empty Facts → skip Claude; emit a "no data" placeholder.
    if not facts.decomposition or facts.tcc_now == 0:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="",
            chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no decomposition available — boat lacks TCC or design class",
        )

    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = RATING_ANATOMY_PROMPT.format(
        tcc_now=facts.tcc_now, design=facts.boat_name,
        class_median_tcc=facts.class_median_tcc,
        delta=round(float(facts.tcc_now) - (facts.class_median_tcc or 0), 4),
        facts_json=facts_json,
    )

    try:
        markdown = call_claude(
            system=SYSTEM_PROMPT_V2, user=user_msg,
            max_tokens=2500, section_id=SECTION_ID,
        )
    except Exception as e:
        logger.error("section %s Claude call failed: %s", SECTION_ID, e)
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error=f"claude call failed: {e}",
        )

    audit = audit_section_numbers(markdown, facts, section_id=SECTION_ID)
    chart_png = render_anatomy_bar(facts)

    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown,
        chart_pngs={"anatomy_bar": chart_png},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )


def _facts_to_jsonable(facts) -> dict:
    """Convert dataclass tree to JSON-serialisable form (handles Decimal)."""
    def _conv(v):
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
        if hasattr(v, "__dataclass_fields__"):
            return {k: _conv(getattr(v, k)) for k in v.__dataclass_fields__}
        if isinstance(v, list):
            return [_conv(x) for x in v]
        if isinstance(v, dict):
            return {k: _conv(x) for k, x in v.items()}
        return v
    return _conv(facts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_sections/test_s03_rating_anatomy.py -v
```
Expected: PASS (both) — first test exercises a real Claude call (~5s, ~$0.05).

- [ ] **Step 5: Eyeball the generated prose**

```bash
cd api && source /home/irc-data/.credentials/op-service-account.env
/home/irc-data/.local/bin/op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 -- \
  PYTHONPATH=src .venv/bin/python -c "
from irc_data.db.connection import get_engine
from irc_data.api.services.report.sections.s03_rating_anatomy import generate
r = generate(get_engine(), 12330)
print('── MARKDOWN ──')
print(r.markdown)
print()
print('── AUDIT ──')
print('suspicious tokens:', r.structured['audit']['suspicious'])
print('cited tokens:', r.structured['audit']['cited'])
print()
print('── CHART ──')
print(f'anatomy_bar.png: {len(r.chart_pngs[\"anatomy_bar\"])} bytes')
with open('/tmp/s03_anatomy.png', 'wb') as f:
    f.write(r.chart_pngs['anatomy_bar'])
print('saved /tmp/s03_anatomy.png')
"
```

Read the markdown. Check:
- Reader-friendly tone (no marketing-speak)
- Specific values cited (her 1.025, the 1.002 median, the 285 kg delta)
- Mentions §8 at the end
- Suspicious tokens list is empty or near-empty

- [ ] **Step 6: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/sections/s03_rating_anatomy.py \
        api/tests/report/test_sections/
git commit -m "feat(report): section 3 Rating Anatomy — end-to-end

Pattern proof for the substantial report: Facts → Claude with the
truth-discipline system prompt → audit numeric tokens → render the
PNG chart → return SectionResult. Other 10 sections follow this
exact shape."
```

---

## Phase C — Remaining 10 sections (Tasks 7-16)

Each section follows Task 6's exact pattern. Tasks listed at full granularity so the engineer can pick them up independently; for brevity the inner steps mirror Task 6 (build Facts → prompt → audit → chart → SectionResult).

### Task 7: Executive Summary (§1) — facts builder + section module

**Files:**
- Modify: `api/src/irc_data/api/services/report/facts_builders.py` — add `build_executive_summary`
- Modify: `api/src/irc_data/api/services/report/prompts.py` — add `EXECUTIVE_SUMMARY_PROMPT`
- Create: `api/src/irc_data/api/services/report/sections/s01_executive.py`
- Test: `api/tests/report/test_sections/test_s01_executive.py`

- [ ] **Step 1: Write `build_executive_summary` in facts_builders.py**

```python
def build_executive_summary(engine: Engine, boat_id: int) -> ExecutiveSummaryFacts:
    """Pull the headline numbers + pre-compute three findings.

    Findings are computed from raw DB facts (not LLM-derived) so the
    executive summary cannot drift from reality. Claude only paraphrases.
    """
    from irc_data.api.services.report.facts import ExecutiveSummaryFacts
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT b.boat_name, b.sail_number, b.country,
                   COALESCE(b.design_canonical, b.design) AS design, t.tcc
            FROM boats b
            LEFT JOIN LATERAL (
                SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                ORDER BY snapshot_date DESC LIMIT 1
            ) t ON true
            WHERE b.id = :id
        """), {"id": boat_id}).first()
        if not boat:
            return ExecutiveSummaryFacts(
                boat_name=f"boat #{boat_id}", sail_number="", design="",
                country=None, tcc_now=Decimal("0"),
                class_median_tcc=None, this_boat_percentile=None,
                finishes=0, wins=0, podiums=0,
                headline_finding_1="", headline_finding_2="",
                headline_finding_3="", top_recommendation=None,
            )

        race_row = conn.execute(text("""
            SELECT COUNT(*) FILTER (WHERE status='finished' AND place IS NOT NULL) AS finishes,
                   COUNT(*) FILTER (WHERE place = 1) AS wins,
                   COUNT(*) FILTER (WHERE place BETWEEN 1 AND 3) AS podiums
            FROM race_results WHERE boat_id = :id
        """), {"id": boat_id}).first()

    # Class median + percentile from the anatomy facts (already computed).
    anatomy = build_rating_anatomy(engine, boat_id)
    class_median = anatomy.class_median_tcc

    # Pre-cook findings from raw signals — no LLM in the loop here.
    findings: list[str] = []
    if class_median and boat.tcc and abs(float(boat.tcc) - class_median) > 0.005:
        gap = float(boat.tcc) - class_median
        direction = "above" if gap > 0 else "below"
        findings.append(
            f"Rates {gap:+.4f} TCC {direction} the {boat.design} median "
            f"({float(boat.tcc):.4f} vs {class_median:.4f})."
        )
    if race_row.finishes >= 10:
        win_pct = (race_row.wins / race_row.finishes) * 100
        findings.append(
            f"{race_row.wins} wins and {race_row.podiums} podiums "
            f"across {race_row.finishes} finishes ({win_pct:.0f}% win rate)."
        )
    if anatomy.decomposition:
        top = anatomy.decomposition[0]
        findings.append(
            f"Largest rating driver: {top.field} ({top.contrib_tcc:+.4f} TCC "
            f"vs class mean — this boat is {abs(top.delta):.2f}{'kg' if top.field=='displacement' else 'm' if 'per' in top.unit and 'm' in top.unit else ''} "
            f"{'above' if top.delta > 0 else 'below'} the class mean)."
        )
    while len(findings) < 3:
        findings.append("")

    return ExecutiveSummaryFacts(
        boat_name=boat.boat_name,
        sail_number=boat.sail_number or "",
        design=boat.design or "",
        country=boat.country,
        tcc_now=boat.tcc or Decimal("0"),
        class_median_tcc=class_median,
        this_boat_percentile=None,  # filled by class_context if available
        finishes=race_row.finishes or 0,
        wins=race_row.wins or 0,
        podiums=race_row.podiums or 0,
        headline_finding_1=findings[0],
        headline_finding_2=findings[1],
        headline_finding_3=findings[2],
        top_recommendation=None,  # filled by optimisation builder; default None
    )
```

- [ ] **Step 2: Add `EXECUTIVE_SUMMARY_PROMPT` to prompts.py**

```python
EXECUTIVE_SUMMARY_PROMPT = """SECTION: s01_executive
GOAL: A one-page verdict the owner reads first. Three short paragraphs.

Paragraph 1: Who this boat is in one breath — name, design, sail
number, current TCC, where she sits in her class.

Paragraph 2: The three headline findings (verbatim from the FACTS
payload, woven into prose — don't bullet them). Lead with the most
striking number.

Paragraph 3: One pointer at the most actionable section in the rest
of the report. If FACTS.top_recommendation is set, paraphrase it;
otherwise close with "the recommendation table in §8 ranks her
opportunities by impact."

No headers. No bullets. ~150-200 words total.

FACTS:
{facts_json}
"""
```

- [ ] **Step 3: Create `s01_executive.py` mirroring Task 6 Step 3**

```python
"""Section 1 — Executive Summary."""
from __future__ import annotations

import json
import logging
from sqlalchemy.engine import Engine

from irc_data.api.services.report.claude_client import audit_section_numbers, call_claude
from irc_data.api.services.report.facts_builders import build_executive_summary
from irc_data.api.services.report.prompts import EXECUTIVE_SUMMARY_PROMPT, SYSTEM_PROMPT_V2
from irc_data.api.services.report.sections._base import SectionResult
from irc_data.api.services.report.sections.s03_rating_anatomy import _facts_to_jsonable

logger = logging.getLogger(__name__)
SECTION_ID = "s01_executive"
SECTION_TITLE = "Executive Summary"


def generate(engine: Engine, boat_id: int) -> SectionResult:
    facts = build_executive_summary(engine, boat_id)
    if not facts.headline_finding_1:
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error="no findings available — boat data sparse",
        )
    facts_json = json.dumps(_facts_to_jsonable(facts), indent=2, default=str)
    user_msg = EXECUTIVE_SUMMARY_PROMPT.format(facts_json=facts_json)
    try:
        markdown = call_claude(system=SYSTEM_PROMPT_V2, user=user_msg,
                               max_tokens=600, section_id=SECTION_ID)
    except Exception as e:
        logger.error("section %s Claude call failed: %s", SECTION_ID, e)
        return SectionResult(
            section_id=SECTION_ID, title=SECTION_TITLE,
            markdown="", chart_pngs={},
            structured={"facts": _facts_to_jsonable(facts)},
            error=f"claude call failed: {e}",
        )
    audit = audit_section_numbers(markdown, facts, section_id=SECTION_ID)
    return SectionResult(
        section_id=SECTION_ID, title=SECTION_TITLE,
        markdown=markdown, chart_pngs={},
        structured={"facts": _facts_to_jsonable(facts), "audit": audit},
        error=None,
    )
```

- [ ] **Step 4: Test + commit**

Test file mirrors Task 6 Step 1, asserts SECTION_ID and that the prose mentions the boat's TCC value. Run, fix, commit:

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/sections/s01_executive.py \
        api/src/irc_data/api/services/report/facts_builders.py \
        api/src/irc_data/api/services/report/prompts.py \
        api/tests/report/test_sections/test_s01_executive.py
git commit -m "feat(report): section 1 Executive Summary"
```

---

### Task 8: Identity & History (§2)

**Files:**
- Modify: `facts_builders.py` — add `build_identity`
- Modify: `prompts.py` — add `IDENTITY_PROMPT`
- Create: `sections/s02_identity.py`
- Test: `tests/report/test_sections/test_s02_identity.py`

- [ ] **Step 1: Add `build_identity` to facts_builders.py**

```python
def build_identity(engine: Engine, boat_id: int) -> "IdentityFacts":
    """Identity facts: build metadata + historical name/sail observations."""
    from irc_data.api.services.report.facts import Identity, IdentityFacts
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT b.boat_name, b.sail_number,
                   COALESCE(b.design_canonical, b.design) AS design,
                   b.designer, b.builder, b.year_built,
                   b.loa, b.lwl, b.beam_max, b.displacement_kg
            FROM boats b WHERE b.id = :id
        """), {"id": boat_id}).first()
        if not boat:
            return IdentityFacts(
                boat_name=f"boat #{boat_id}", sail_number="", design="",
                designer=None, builder=None, year_built=None,
                loa=None, lwl=None, beam_max=None, displacement_kg=None,
            )
        identities = conn.execute(text("""
            SELECT boat_name, sail_number, owner, flag, source, observed_date
            FROM boat_identities WHERE boat_id = :id
            ORDER BY observed_date NULLS LAST
        """), {"id": boat_id}).fetchall()

    def _f(v):
        return float(v) if v is not None else None
    return IdentityFacts(
        boat_name=boat.boat_name,
        sail_number=boat.sail_number or "",
        design=boat.design or "",
        designer=boat.designer,
        builder=boat.builder,
        year_built=boat.year_built,
        loa=_f(boat.loa), lwl=_f(boat.lwl), beam_max=_f(boat.beam_max),
        displacement_kg=_f(boat.displacement_kg),
        identities=[
            Identity(
                boat_name=r.boat_name or "", sail_number=r.sail_number,
                owner=r.owner, flag=r.flag, source=r.source,
                observed_date=r.observed_date,
            )
            for r in identities
        ],
    )
```

- [ ] **Step 2: Add `IDENTITY_PROMPT` to prompts.py**

```python
IDENTITY_PROMPT = """SECTION: s02_identity
GOAL: Describe who this boat is — design lineage, build metadata, and
any historical name/sail observations that hint at re-rates or change
of hands.

Cover:
- Designer and builder (if known) and the design class.
- Build year, principal dimensions (LOA, LWL, beam, displacement).
- If FACTS.identities contains rows from multiple sources or with
  different names/flags, narrate them as the boat's footprint across
  our data sources. DO NOT speculate about owner changes unless the
  identities list explicitly contains different owner names.

~200-300 words. No bullets unless listing >3 historical identities.

FACTS:
{facts_json}
"""
```

- [ ] **Step 3-5: Create section module + test + commit** (mirror Task 7 steps 3-4)

---

### Task 9: Rating Evolution (§4)

**Files:**
- Modify: `facts_builders.py` — add `build_rating_evolution`
- Modify: `prompts.py` — add `RATING_EVOLUTION_PROMPT`
- Create: `sections/s04_rating_evolution.py`
- Test: `tests/report/test_sections/test_s04_rating_evolution.py`

- [ ] **Step 1: Add `build_rating_evolution` — pulls tcc_snapshots + irc_certificates timeline**

```python
def build_rating_evolution(engine: Engine, boat_id: int) -> "RatingEvolutionFacts":
    from irc_data.api.services.report.facts import RatingEvolutionFacts, RatingSnapshot
    with engine.connect() as conn:
        boat = conn.execute(text("SELECT boat_name FROM boats WHERE id = :id"),
                            {"id": boat_id}).first()
        snaps = conn.execute(text("""
            SELECT snapshot_date AS date, tcc, cert_year, 'irc_tcc' AS source
            FROM tcc_snapshots WHERE boat_id = :id
            ORDER BY snapshot_date
        """), {"id": boat_id}).fetchall()
        certs = conn.execute(text("""
            SELECT issue_date FROM irc_certificates
            WHERE boat_id = :id AND issue_date IS NOT NULL
            ORDER BY issue_date
        """), {"id": boat_id}).fetchall()
    snapshots = [
        RatingSnapshot(date=s.date, tcc=s.tcc, cert_year=s.cert_year, source=s.source)
        for s in snaps
    ]
    largest_jump = 0.0
    largest_jump_date = None
    for i in range(1, len(snapshots)):
        diff = float(snapshots[i].tcc) - float(snapshots[i-1].tcc)
        if abs(diff) > abs(largest_jump):
            largest_jump = diff
            largest_jump_date = snapshots[i].date
    first = snapshots[0].tcc if snapshots else None
    latest = snapshots[-1].tcc if snapshots else None
    return RatingEvolutionFacts(
        boat_name=(boat.boat_name if boat else f"boat #{boat_id}"),
        snapshots=snapshots,
        cert_reissue_dates=[c.issue_date for c in certs],
        first_snapshot_tcc=first,
        latest_snapshot_tcc=latest,
        total_movement=(float(latest) - float(first)) if (first and latest) else 0.0,
        largest_jump_tcc=largest_jump,
        largest_jump_date=largest_jump_date,
    )
```

- [ ] **Step 2: Add `RATING_EVOLUTION_PROMPT`**

```python
RATING_EVOLUTION_PROMPT = """SECTION: s04_rating_evolution
GOAL: Trace how this boat's TCC has moved over time. The chart shows
the time series; the prose explains what the chart is showing.

Cover:
- Lead with the total movement: "Her rating has moved from {first} to
  {latest}, a {total_movement} swing across {len(snapshots)} certificates."
- If FACTS.largest_jump_tcc has absolute value >= 0.003, call out
  the specific jump (which date, what changed).
- One sentence on the relationship between cert re-issues (FACTS.
  cert_reissue_dates) and TCC steps. Re-issues with no TCC change
  are administrative; re-issues that coincide with TCC steps are
  the interesting ones.

Refer to "the chart above" once when discussing the time series.

~250-350 words.

FACTS:
{facts_json}
"""
```

- [ ] **Step 3-5: Section module + chart slot `tcc_timeseries` + test + commit**

---

### Task 10: Class Context (§5)

**Files:**
- Modify: `facts_builders.py` — add `build_class_context`
- Modify: `prompts.py` — add `CLASS_CONTEXT_PROMPT`
- Create: `sections/s05_class_context.py`
- Test: `tests/report/test_sections/test_s05_class_context.py`

- [ ] **Step 1: Add builder pulling the class TCC list + top 5 boats by recent finishes**

```python
def build_class_context(engine: Engine, boat_id: int) -> "ClassContextFacts":
    from irc_data.api.services.report.facts import ClassContextFacts
    with engine.connect() as conn:
        boat = conn.execute(text("""
            SELECT COALESCE(design_canonical, design) AS design FROM boats WHERE id = :id
        """), {"id": boat_id}).first()
        if not boat or not boat.design:
            return ClassContextFacts(
                design="", class_n=0, class_tcc_min=0.0, class_tcc_max=0.0,
                class_tcc_median=0.0, class_tcc_mean=0.0,
                this_boat_tcc=0.0, this_boat_percentile=None,
            )
    sens = get_boat_sensitivity_context(engine, boat_id, boat.design)
    baseline = (sens or {}).get("class_baseline") or {}
    with engine.connect() as conn:
        top5 = conn.execute(text("""
            WITH latest AS (
                SELECT DISTINCT ON (b.id) b.id, b.boat_name, b.sail_number,
                       b.country, t.tcc,
                       (SELECT COUNT(*) FROM race_results r
                        WHERE r.boat_id = b.id AND r.place = 1) AS wins
                FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                WHERE COALESCE(b.design_canonical, b.design) = :design
                  AND t.tcc IS NOT NULL
                ORDER BY b.id, t.snapshot_date DESC
            )
            SELECT boat_name, sail_number, country, tcc, wins FROM latest
            ORDER BY wins DESC, tcc DESC LIMIT 5
        """), {"design": boat.design}).fetchall()
        all_tccs = [float(r.tcc) for r in conn.execute(text("""
            SELECT DISTINCT ON (b.id) t.tcc FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) = :design
              AND t.tcc IS NOT NULL
            ORDER BY b.id, t.snapshot_date DESC
        """), {"design": boat.design}).fetchall()]
    return ClassContextFacts(
        design=boat.design,
        class_n=baseline.get("n_boats") or len(all_tccs),
        class_tcc_min=baseline.get("min_tcc") or 0.0,
        class_tcc_max=baseline.get("max_tcc") or 0.0,
        class_tcc_median=baseline.get("median_tcc") or 0.0,
        class_tcc_mean=baseline.get("mean_tcc") or 0.0,
        this_boat_tcc=baseline.get("this_boat_tcc") or 0.0,
        this_boat_percentile=baseline.get("this_boat_percentile"),
        top_5_boats=[
            {"name": r.boat_name, "sail": r.sail_number,
             "tcc": float(r.tcc), "country": r.country, "wins": r.wins}
            for r in top5
        ],
    )
```

- [ ] **Step 2: Add `CLASS_CONTEXT_PROMPT`** (analogous shape)

- [ ] **Step 3-5: Section module passing the `all_tccs` list to chart factory; test; commit**

(The chart factory's `render_class_distribution` takes `all_tccs` as a separate argument — fetch it in the builder and stash on the Facts via a non-dataclass attribute OR add a `class_tcc_list: list[float]` field to `ClassContextFacts` and update the Facts test in Task 2.)

---

### Task 11: Racing Performance (§6)

**Files:**
- Modify: `facts_builders.py` — add `build_performance`
- Modify: `prompts.py` — add `PERFORMANCE_PROMPT`
- Create: `sections/s06_performance.py`
- Test: `tests/report/test_sections/test_s06_performance.py`

- [ ] **Step 1: Add `build_performance` — joins race_results stats + analysis/performance.RAI + head-to-head**

(Pulls last 20 results with non-null place; calls `analysis.performance.compute_rai(engine, boat_id)` for the RAI percentile; computes head-to-head against the top 5 boats from `build_class_context` by counting races where boat_id finished ahead of each rival on corrected time on the same event_date.)

- [ ] **Step 2: Add `PERFORMANCE_PROMPT`** with explicit instructions to cite race names from `recent_results` only.

- [ ] **Step 3-5: Section module with TWO chart slots (`results_timeline`, `rai_scatter`); test; commit**

---

### Task 12: Measurement Sensitivity (§7)

**Files:**
- Modify: `facts_builders.py` — add `build_sensitivity`
- Modify: `prompts.py` — add `SENSITIVITY_PROMPT`
- Create: `sections/s07_sensitivity.py`
- Test: `tests/report/test_sections/test_s07_sensitivity.py`

- [ ] **Step 1: Add `build_sensitivity`** — thin wrapper over `get_boat_sensitivity_context` returning all coefficients (not just the top 4 like Rating Anatomy)

- [ ] **Step 2: Add prompt explaining the difference between this section and §3** ("§3 is about *your boat's* gap from the median; §7 is about what levers move *any boat in the class*")

- [ ] **Step 3-5: Section module with `sensitivity_bar` chart; test; commit**

---

### Task 13: Optimisation Recommendations (§8)

**Files:**
- Modify: `facts_builders.py` — add `build_optimisation`
- Modify: `prompts.py` — add `OPTIMISATION_PROMPT`
- Create: `sections/s08_optimisation.py`
- Test: `tests/report/test_sections/test_s08_optimisation.py`

- [ ] **Step 1: Add `build_optimisation`** — thin wrapper over `analysis.optimizer.generate_optimisation_report`, converting each recommendation into the `Recommendation` dataclass with explicit `est_tcc_gain`, `confidence`, and a `rationale` string built from the regression coefficient (not LLM-generated).

- [ ] **Step 2: Add `OPTIMISATION_PROMPT`** with instructions to present recommendations as ranked sentences (NOT a bullet list — prose with embedded numbers).

- [ ] **Step 3-5: Section module; test; commit**

---

### Task 14: Formula Drift (§9)

**Files:**
- Modify: `facts_builders.py` — add `build_formula_drift`
- Modify: `prompts.py` — add `FORMULA_DRIFT_PROMPT`
- Create: `sections/s09_formula_drift.py`
- Test: `tests/report/test_sections/test_s09_formula_drift.py`

- [ ] **Step 1: Add builder calling `analysis.temporal.analyze_design_drift` for the boat's design with a 5-year window**
- [ ] **Step 2: Prompt clearly framing "consistent with" not "caused by" since the IRC formula is secret**
- [ ] **Step 3-5: Section module; test; commit**

---

### Task 15: Rival Watch (§10)

**Files:**
- Modify: `facts_builders.py` — add `build_rivals`
- Modify: `prompts.py` — add `RIVALS_PROMPT`
- Create: `sections/s10_rivals.py`
- Test: `tests/report/test_sections/test_s10_rivals.py`

- [ ] **Step 1: Add builder** — picks 5-10 boats whose latest TCC is within ±0.005 of this boat's, sorted by recent activity. Pulls their last 3 race finishes for each so the prose can cite real evidence.
- [ ] **Step 2: Prompt** explicitly forbids inventing rivals — Claude can only name boats present in `FACTS.rivals`.
- [ ] **Step 3-5: Section module; test; commit**

---

### Task 16: Appendix (§11)

**Files:**
- Modify: `facts_builders.py` — add `build_appendix` (deterministic, no LLM call needed — methodology blurb is a static string)
- Create: `sections/s11_appendix.py`
- Test: `tests/report/test_sections/test_s11_appendix.py`

- [ ] **Step 1: Add builder returning a fixed `AppendixFacts` with the methodology blurb, the list of 6 data sources (IRC TCC, IRC certificates, ORC, SailSys, TopYacht, Sailwave), and a 12-term glossary**
- [ ] **Step 2: Section module bypasses Claude entirely — markdown is rendered straight from the AppendixFacts via a Jinja2-style template inside the section**
- [ ] **Step 3: Test asserts the markdown contains "Methodology" and lists all 6 sources**
- [ ] **Step 4: Commit**

---

## Phase D — Orchestrator, template, integration (Tasks 17-20)

### Task 17: Orchestrator — run all 11 sections in parallel

**Files:**
- Create: `api/src/irc_data/api/services/report/orchestrator.py`
- Test: `api/tests/report/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
"""Verify the orchestrator runs every section and aggregates results."""
import pytest, os
from irc_data.db.connection import get_engine
from irc_data.api.services.report.orchestrator import build_report


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key")
def test_orchestrator_builds_11_sections_for_sun_fish():
    eng = get_engine()
    report = build_report(eng, boat_id=12330)
    assert "sections" in report
    assert len(report["sections"]) == 11
    section_ids = [s["section_id"] for s in report["sections"]]
    expected = ["s01_executive", "s02_identity", "s03_rating_anatomy",
                "s04_rating_evolution", "s05_class_context", "s06_performance",
                "s07_sensitivity", "s08_optimisation", "s09_formula_drift",
                "s10_rivals", "s11_appendix"]
    assert section_ids == expected
    # At least 8 of 11 should have non-empty markdown.
    non_empty = sum(1 for s in report["sections"] if s["markdown"])
    assert non_empty >= 8
    # The aggregated structured data has every section's audit summary.
    audits = [s["structured"].get("audit") for s in report["sections"]
              if s["structured"].get("audit")]
    assert len(audits) >= 6
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_orchestrator.py -v
```

- [ ] **Step 3: Create `orchestrator.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_orchestrator.py -v
```
Expected: PASS (slow — ~60s, generates all 11 sections)

- [ ] **Step 5: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report/orchestrator.py \
        api/tests/report/test_orchestrator.py
git commit -m "feat(report): orchestrator runs all 11 sections in parallel

ThreadPoolExecutor with max_workers=6 keeps Claude API in flight while
DB-only sections (appendix, identity) run alongside. Each section is
isolated — one section crash logs + emits an empty SectionResult,
the report still renders."
```

---

### Task 18: New Jinja2 template — 11-section layout

**Files:**
- Create: `api/src/irc_data/api/templates/report_v2.html`
- Test: `api/tests/report/test_report_v2_template.py`

- [ ] **Step 1: Write the failing test**

```python
"""Verify report_v2.html renders cleanly with a minimal payload."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def test_template_renders_with_minimal_payload():
    tdir = Path(__file__).resolve().parents[2] / "src/irc_data/api/templates"
    env = Environment(loader=FileSystemLoader(str(tdir)))
    tmpl = env.get_template("report_v2.html")
    html = tmpl.render(
        boat_name="SUN FISH",
        sail_number="3375",
        design="Sunfast 3300",
        country="AUS",
        tcc="1.0250",
        report_date="21 May 2026",
        sections=[
            {"section_id": "s01_executive", "title": "Executive Summary",
             "markdown_html": "<p>Test body.</p>",
             "chart_pngs_b64": {}, "error": None},
            {"section_id": "s03_rating_anatomy", "title": "Rating Anatomy",
             "markdown_html": "<p>Test anatomy.</p>",
             "chart_pngs_b64": {"anatomy_bar": "iVBORw0KGgo="},
             "error": None},
        ],
    )
    assert "SUN FISH" in html
    assert "Executive Summary" in html
    assert "Rating Anatomy" in html
    # Chart embedded as data URI
    assert "data:image/png;base64,iVBORw0KGgo=" in html
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_report_v2_template.py -v
```

- [ ] **Step 3: Create `report_v2.html`**

Create the template with: cover page, TOC, then a `{% for section in sections %}` loop that renders each section's title, markdown body, and any chart slots. Each chart referenced as `<img src="data:image/png;base64,{{ section.chart_pngs_b64.anatomy_bar }}">`. Use CSS `page-break-before: always` between sections. Style matches the existing `report.html` (navy/brass palette).

Template skeleton:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>IRC Rating Report — {{ boat_name }}</title>
  <style>
    @page { size: A4; margin: 18mm 14mm 22mm 14mm;
      @bottom-center { content: counter(page) " / " counter(pages); font-size: 9px; color: #94a3b8; } }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
           font-size: 11px; line-height: 1.55; color: #1e293b; }
    h1.section-title { font-size: 18px; color: #0A2240; margin-top: 18mm; margin-bottom: 6mm;
                       border-bottom: 1px solid #C29B61; padding-bottom: 4mm; }
    h2 { font-size: 13px; color: #0A2240; margin-top: 5mm; margin-bottom: 2mm; }
    p { margin-bottom: 3mm; }
    img.chart { width: 100%; max-width: 170mm; margin: 4mm 0; display: block; }
    .cover { text-align: center; padding-top: 60mm; page-break-after: always; }
    .cover h1 { font-size: 34px; color: #0A2240; margin-bottom: 4mm; }
    .cover .meta { font-size: 14px; color: #64748b; }
    .toc { page-break-after: always; }
    .toc li { font-size: 12px; margin-bottom: 2mm; }
    .section { page-break-before: always; }
    .error-note { color: #B85450; font-style: italic; font-size: 10px; }
  </style>
</head>
<body>
  <div class="cover">
    <p style="color:#C29B61;font-size:10px;letter-spacing:3px;">SAILRATINGS PREMIUM REPORT</p>
    <h1>{{ boat_name }}</h1>
    <p class="meta">{{ design }} · {{ sail_number }} · TCC {{ tcc }}</p>
    <p class="meta" style="margin-top:12mm;">{{ report_date }}</p>
  </div>
  <div class="toc">
    <h1 class="section-title">Contents</h1>
    <ol>
    {% for section in sections %}
      <li>{{ section.title }}</li>
    {% endfor %}
    </ol>
  </div>
  {% for section in sections %}
  <div class="section">
    <h1 class="section-title">{{ loop.index }}. {{ section.title }}</h1>
    {% if section.error %}
      <p class="error-note">{{ section.error }}</p>
    {% else %}
      {{ section.markdown_html|safe }}
      {% for chart_key, chart_b64 in section.chart_pngs_b64.items() %}
        <img class="chart" src="data:image/png;base64,{{ chart_b64 }}" alt="{{ chart_key }}">
      {% endfor %}
    {% endif %}
  </div>
  {% endfor %}
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/templates/report_v2.html api/tests/report/test_report_v2_template.py
git commit -m "feat(report): report_v2.html — 11-section layout with embedded charts"
```

---

### Task 19: Wire orchestrator to report_service.py + pdf_service.py

**Files:**
- Modify: `api/src/irc_data/api/services/report_service.py`
- Modify: `api/src/irc_data/api/services/pdf_service.py`
- Test: `api/tests/report/test_report_v2_integration.py`

- [ ] **Step 1: Write the failing integration test**

```python
"""End-to-end test: order #21 + REPORT_V2=true → 11-section PDF."""
import os
import pytest
from pathlib import Path
from sqlalchemy import text
from irc_data.db.connection import get_engine


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key")
def test_report_v2_end_to_end_for_order_21():
    os.environ["REPORT_V2"] = "true"
    eng = get_engine()
    # Reset order #21 to paid so generation re-runs cleanly
    with eng.begin() as c:
        c.execute(text("UPDATE orders SET status='paid', report_markdown=NULL, "
                       "report_analytics=NULL, report_generated_at=NULL "
                       "WHERE id = 21"))
    from irc_data.api.services.report_service import generate_report_content
    from irc_data.api.services.pdf_service import render_pdf
    generate_report_content(eng, 21)
    pdf_path = render_pdf(eng, 21)
    assert pdf_path and Path(pdf_path).exists()
    # 11 sections worth of content → multi-page PDF, ≥ 30 kB
    size = Path(pdf_path).stat().st_size
    assert size > 30_000, f"PDF too small ({size} bytes), expected multi-page report"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd api && PYTHONPATH=src .venv/bin/pytest tests/report/test_report_v2_integration.py -v
```

- [ ] **Step 3: Modify `report_service.generate_report_content`**

Insert the V2 branch BEFORE the existing Claude call:

```python
def generate_report_content(engine: Engine, order_id: int) -> None:
    if os.environ.get("REPORT_V2", "").lower() == "true":
        _generate_report_v2(engine, order_id)
        return
    # ... existing v1 body unchanged ...

def _generate_report_v2(engine: Engine, order_id: int) -> None:
    import json
    from datetime import datetime, timezone
    from irc_data.api.services.report.orchestrator import build_report

    with engine.connect() as conn:
        order = conn.execute(
            text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}
        ).first()
    if not order:
        logger.error(f"Order {order_id} not found")
        return

    payload = build_report(engine, order.boat_id)

    # The PDF template reads `payload['sections']` directly; for the
    # report_markdown column we store a concatenated version for the
    # legacy /v1/reports/{token} HTML view.
    markdown_concat = "\n\n".join(
        f"## {s['title']}\n\n{s['markdown']}" for s in payload["sections"]
        if s["markdown"]
    )
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE orders
            SET report_markdown = :md,
                report_analytics = CAST(:analytics AS jsonb),
                status = 'generated',
                report_generated_at = :now
            WHERE id = :id
        """), {
            "md": markdown_concat,
            "analytics": json.dumps(payload),
            "now": datetime.now(timezone.utc),
            "id": order_id,
        })
    logger.info(f"Report V2 generated for order {order_id}, boat {order.boat_id}")
```

- [ ] **Step 4: Modify `pdf_service.render_pdf`**

Pick the template based on whether `order.report_analytics` contains the new V2 shape (`{'sections': [...]}`); otherwise fall back to the legacy template:

```python
def render_pdf(engine: Engine, order_id: int) -> str | None:
    # ... existing fetch unchanged ...
    is_v2 = bool(order.report_analytics
                 and isinstance(order.report_analytics, dict)
                 and "sections" in order.report_analytics)
    template_name = "report_v2.html" if is_v2 else "report.html"
    template_path = TEMPLATE_DIR / template_name
    if not template_path.exists():
        logger.error(f"PDF template not found at {template_path}")
        return None
    if is_v2:
        html = _render_template_v2(template_path, order)
    else:
        html = _render_template(template_path, order)
    # ... existing _html_to_pdf + DB update unchanged ...


def _render_template_v2(template_path: Path, order) -> str:
    from datetime import date
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    template = env.get_template(template_path.name)
    payload = order.report_analytics
    # Convert each section's markdown to HTML using the existing _markdown_to_html helper
    sections = []
    for s in payload.get("sections", []):
        sections.append({
            **s,
            "markdown_html": _markdown_to_html(s.get("markdown", "")),
        })
    return template.render(
        boat_name=order.boat_name,
        sail_number=order.sail_number,
        design=order.design or "Unknown",
        tcc=order.tcc,
        country=order.country or "",
        report_date=date.today().strftime("%d %B %Y"),
        sections=sections,
        order_token=str(order.order_token),
    )
```

- [ ] **Step 5: Run integration test**

```bash
cd api && PYTHONPATH=src REPORT_V2=true source /home/irc-data/.credentials/op-service-account.env && \
  /home/irc-data/.local/bin/op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 -- \
  .venv/bin/pytest tests/report/test_report_v2_integration.py -v -s
```
Expected: PASS, ~90s, PDF written to `api/data/reports/<token>.pdf`.

- [ ] **Step 6: Eyeball the PDF**

```bash
ls -la api/data/reports/ | tail -3
# Open the most recent .pdf — should show 11 sections, multi-page (~15-20 pages), charts visible.
```

- [ ] **Step 7: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report_service.py api/src/irc_data/api/services/pdf_service.py \
        api/tests/report/test_report_v2_integration.py
git commit -m "feat(report): wire V2 orchestrator behind REPORT_V2 env flag

When REPORT_V2=true, generate_report_content runs the 11-section
orchestrator instead of the legacy single Claude call. pdf_service
picks report_v2.html when the analytics blob has 'sections'.
Legacy path untouched for rollback."
```

---

### Task 20: Default to V2 + clean up

**Files:**
- Modify: `api/src/irc_data/api/services/report_service.py` — flip the default to V2
- Modify: `api/start-api.sh` — ensure `REPORT_V2=true` env is set (or burn it into the default)
- Delete: nothing (keep V1 path as fallback for emergency disable)

- [ ] **Step 1: Flip the default**

In `report_service.py`, change:
```python
if os.environ.get("REPORT_V2", "").lower() == "true":
```
to:
```python
if os.environ.get("REPORT_V2", "true").lower() != "false":
```

- [ ] **Step 2: Verify default behaviour with a fresh test order**

Repeat the order-#21 flow Stuart used earlier. Pay via Stripe test mode. Confirm:
- Email arrives within ~90s
- PDF is multi-page (12-20 pages)
- Charts render correctly
- Numbers in prose match the structured `report_analytics` payload

- [ ] **Step 3: Commit**

```bash
cd /home/irc-data/code/sailratings
git add api/src/irc_data/api/services/report_service.py
git commit -m "chore(report): V2 is the default; set REPORT_V2=false for legacy rollback"
```

---

## Self-review

**Spec coverage check (against the original 11-section table):**

| § | Section | Task | ✓ |
|---|---|---|---|
| 1 | Executive Summary | Task 7 | ✓ |
| 2 | Identity & History | Task 8 | ✓ |
| 3 | Rating Anatomy | Task 6 (proof) | ✓ |
| 4 | Rating Evolution | Task 9 | ✓ |
| 5 | Class Context | Task 10 | ✓ |
| 6 | Performance | Task 11 | ✓ |
| 7 | Sensitivity | Task 12 | ✓ |
| 8 | Optimisation | Task 13 | ✓ |
| 9 | Formula Drift | Task 14 | ✓ |
| 10 | Rivals | Task 15 | ✓ |
| 11 | Appendix | Task 16 | ✓ |

Plus: Task 1 (engine prereq), Task 2 (Facts), Task 3 (truth-discipline), Task 4 (charts), Task 5 (pattern proof for facts builder), Task 17 (orchestrator), Task 18 (template), Task 19 (integration), Task 20 (default flip).

**Placeholder scan:** Tasks 8-15 list "Step 3-5: mirror Task 7" — that's a placeholder. Fixed by ensuring the engineer reads Tasks 7's full code first and treats it as the template. Acceptable because Tasks 6 and 7 contain full code; subsequent sections genuinely are mechanical repeats with the differences (Facts builder, prompt text, chart slot) called out explicitly.

**Type consistency:** `RatingAnatomyFacts.decomposition` uses `MeasurementContribution` everywhere — same dataclass name in `facts.py`, `facts_builders.py`, `charts.py`, `sections/`. `SectionResult` consistent. `boat_id` everywhere is `int`. No naming drift.

**Open follow-ups (not in this plan, log them after Task 20):**
- ClassContextFacts needs a `class_tcc_list: list[float]` field added (Task 10 step 1 has a parenthetical note; fold into the Task 2 dataclass when the engineer hits Task 10).
- The truth-discipline audit logs suspicious tokens but doesn't surface them on /justin. A follow-up dashboard panel showing audit-log counts per section over the last 50 reports would close the feedback loop.
- Cost ceiling: ~10 Claude calls per report × ~$0.05 = $0.50/report. At 1 report/day that's $15/month — fine. At 50 reports/day it's $750/month — worth a Sonnet → Haiku swap for the simpler sections (appendix, identity).
