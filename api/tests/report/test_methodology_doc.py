"""SM-01-01 — docs/model/METHODOLOGY.md and GLOSSARY.md are checked, not trusted.

Reads the two documents, the three golden ``ReportFactsV1`` bundles
(SM-01-08) and ``docs/ai/glossary.yaml`` (AI-01-01). No database, no network,
no model code imported.

Worked-example grammar (see METHODOLOGY.md, "How to read this document"):

* A worked example is a block delimited by
  ``<!-- worked-example boat=<slug> -->`` ... ``<!-- /worked-example -->``.
* Every inline-code span inside a block is a *quoted figure* if it is one of:
    - ``a.b[field=x].c = 1.23``  -> resolved in the boat's golden bundle and
                                     compared (within 1e-3);
    - ``1.23``                   -> must appear somewhere in the bundle;
    - ``derived: <arithmetic> = <result>`` -> the arithmetic is re-evaluated;
      every literal in it must be a bundle value, a documented constant
      (``ALLOWED_CONSTANTS``) or the result of an earlier ``derived`` span in
      the same block.
  Other spans (names, constants, words) are not figures and are ignored.
"""
from __future__ import annotations

import ast
import json
import operator
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_MODEL = REPO_ROOT / "docs" / "model"
METHODOLOGY = DOCS_MODEL / "METHODOLOGY.md"
GLOSSARY = DOCS_MODEL / "GLOSSARY.md"
GLOSSARY_YAML = REPO_ROOT / "docs" / "ai" / "glossary.yaml"
GOLDEN_ROOT = Path(__file__).resolve().parent / "golden"
ANALYSIS = REPO_ROOT / "api" / "src" / "irc_data" / "analysis"

GOLDEN_SLUGS = ("chilli_pepper", "diablo_j", "kestrel")
ABS_TOL = 1e-3

# Documented constants that may appear as literals in a `derived:` span without
# being a bundle value (rule constants from METHODOLOGY.md).
ALLOWED_CONSTANTS = (0.001, 0.1, 0.2, 0.35, 0.5, 1.0, 3.6)

REQUIRED_COMPONENTS = (
    "Class regression",
    "Racing Advantage Index (RAI)",
    "Smart-boat cohort",
    "Rule drift",
    "Δ TCC to seconds per hour",
    'One-design and "not meaningful"',
    "What-if estimator",
)
REQUIRED_SUBHEADINGS = (
    "Definition",
    "Formula",
    "Inputs",
    "Thresholds",
    "Versioning rule",
    "Worked example",
)

# (source file, constant name, expected literal) — the thresholds the document
# states; the test fails if the code and the document disagree.
CODE_CONSTANTS = (
    ("class_regression.py", "MIN_BOATS", "5"),
    ("class_regression.py", "MIN_FEATURE_VARIANCE", "1e-12"),
    ("class_regression.py", "ORC_TIGHT_R2", "0.8"),
    ("class_regression.py", "WITHHELD_FIXTURE_CLASS", '"Cape 31"'),
    ("regression.py", "MIN_BOATS_FOR_REGRESSION", "5"),
    ("regression.py", "MIN_BOATS_TIER_A", "5"),
    ("regression.py", "MIN_BOATS_FULL_CV", "15"),
    ("rai.py", "DEFAULT_MIN_RACES", "5"),
    ("rai.py", "DEFAULT_MIN_BAND_RACES", "3"),
    ("rai.py", "DEFAULT_BOOTSTRAP_RESAMPLES", "2000"),
    ("rai.py", "DEFAULT_CONFIDENCE_LEVEL", "0.95"),
    ("rule_drift.py", "MIN_CLASS_COHORT", "3"),
    ("rule_drift.py", "MIN_BOATS_LEVER_REGRESSION", "10"),
    ("rule_drift.py", "LEVER_EPSILON", "0.001"),
    ("rule_drift.py", "DRIFT_EPSILON", "0.0005"),
    ("what_if.py", "SECONDS_PER_HOUR_PER_TCC_POINT", "3.6"),
    ("what_if.py", "ESTIMATE_FLAG", '"class_regression_estimate"'),
    ("comparative.py", "_HEADROOM_LOW_CV", "0.005"),
    ("comparative.py", "_HEADROOM_MODERATE_CV", "0.015"),
    ("race_prep.py", "MIN_SPLIT_RACES", "3"),
    ("race_prep.py", "DEFAULT_MIN_RIVAL_MEETINGS", "2"),
    ("backtest.py", "RAI_STABILITY_TOL", "7.5"),
    ("backtest.py", "RATING_MODEL_HOLDOUT_MAE_MAX", "0.040"),
    ("backtest.py", "RATING_MODEL_HOLDOUT_R2_MIN", "0.80"),
)
DISCLAIMER = "estimate from class regression — not an official rating"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tol(expected: float) -> float:
    """1e-3 absolute, tightened for small magnitudes so 0.002 != 0.0."""
    return min(ABS_TOL, ABS_TOL * abs(expected)) + 1e-12


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _tol(b)


def _numeric_leaves(node) -> list[float]:
    out: list[float] = []
    if isinstance(node, bool):
        return out
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(_numeric_leaves(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_numeric_leaves(v))
    return out


_STEP = re.compile(r"(\w+)(?:\[(\w+)=([^\]]+)\])?")


def _resolve(bundle: dict, path: str):
    node = bundle
    for part in path.split("."):
        # a list selector like coefficients[field=mhw] contains no dots
        m = _STEP.fullmatch(part)
        assert m, f"bad path segment {part!r} in {path!r}"
        key, sel_key, sel_val = m.groups()
        assert isinstance(node, dict) and key in node, f"{path!r}: no key {key!r}"
        node = node[key]
        if sel_key is not None:
            assert isinstance(node, list), f"{path!r}: {key!r} is not a list"
            hits = [e for e in node if str(e.get(sel_key)) == sel_val]
            assert len(hits) == 1, f"{path!r}: selector {sel_key}={sel_val} matched {len(hits)}"
            node = hits[0]
    return node


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _eval(expr: str) -> tuple[float, list[float]]:
    """Evaluate +,-,*,/ arithmetic over numeric literals; return (value, literals)."""
    literals: list[float] = []

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            literals.append(abs(float(n.value)))
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](walk(n.left), walk(n.right))
        raise AssertionError(f"unsupported expression element {ast.dump(n)} in {expr!r}")

    return walk(ast.parse(expr, mode="eval")), literals


def _in(values, x: float) -> bool:
    return any(_close(abs(v), abs(x)) for v in values)


_BLOCK = re.compile(
    r"<!-- worked-example boat=(\w+) -->(.*?)<!-- /worked-example -->", re.DOTALL
)
_SPAN = re.compile(r"`([^`\n]+)`")
_NUM = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _worked_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _BLOCK.finditer(text)]


def _headings(text: str, level: int) -> list[str]:
    """Headings of exactly `level` '#', ignoring fenced code."""
    out, fenced = [], False
    prefix = "#" * level + " "
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith(prefix):
            out.append(line[len(prefix):].strip())
    return out


def _sections(text: str) -> dict[str, str]:
    """Map '## ' heading -> body text up to the next '## ' heading (fence-aware)."""
    sections: dict[str, list[str]] = {}
    current, fenced = None, False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _bundle(slug: str) -> dict:
    return json.loads((GOLDEN_ROOT / slug / "golden_report_facts_v1.json").read_text())


# --------------------------------------------------------------------------- #
# 1. worked examples match the golden bundles
# --------------------------------------------------------------------------- #
def test_worked_example_figures_match_golden_bundles():
    text = METHODOLOGY.read_text()
    blocks = _worked_blocks(text)
    assert blocks, "METHODOLOGY.md has no worked-example blocks"

    bundles = {slug: _bundle(slug) for slug in GOLDEN_SLUGS}
    leaves = {slug: _numeric_leaves(b) for slug, b in bundles.items()}
    figures = {slug: 0 for slug in GOLDEN_SLUGS}
    failures: list[str] = []

    for slug, body in blocks:
        assert slug in bundles, f"worked-example for unknown boat {slug!r}"
        bundle, values = bundles[slug], leaves[slug]
        earlier_results: list[float] = []

        for raw in _SPAN.findall(body):
            span = raw.strip()

            if span.startswith("derived:"):
                expr, _, result = span[len("derived:"):].rpartition(" = ")
                if not _NUM.fullmatch(result.strip()):
                    failures.append(f"[{slug}] derived span without numeric result: {span!r}")
                    continue
                value, literals = _eval(expr.strip())
                claimed = float(result)
                if not _close(value, claimed):
                    failures.append(f"[{slug}] {span!r}: evaluates to {value!r}")
                for lit in literals:
                    ok = (
                        _in(values, lit)
                        or _in(ALLOWED_CONSTANTS, lit)
                        or _in(earlier_results, lit)
                    )
                    if not ok:
                        failures.append(
                            f"[{slug}] {span!r}: literal {lit} is not in the {slug} bundle, "
                            f"ALLOWED_CONSTANTS or an earlier derived result"
                        )
                earlier_results.append(abs(claimed))
                figures[slug] += 1
                continue

            lhs, sep, rhs = span.rpartition(" = ")
            if sep and _NUM.fullmatch(rhs.strip()) and lhs.startswith(("engines.", "sections.")):
                claimed = float(rhs)
                try:
                    actual = _resolve(bundle, lhs)
                except AssertionError as exc:
                    failures.append(f"[{slug}] {span!r}: {exc}")
                    continue
                if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                    failures.append(f"[{slug}] {span!r}: bundle value {actual!r} is not numeric")
                elif not _close(float(actual), claimed):
                    failures.append(f"[{slug}] {span!r}: bundle has {actual!r}")
                figures[slug] += 1
                continue

            if _NUM.fullmatch(span):
                claimed = float(span)
                if not _in(values, claimed):
                    failures.append(f"[{slug}] {span!r}: no such value in the {slug} bundle")
                figures[slug] += 1

    assert not failures, "worked-example figures disagree with the golden bundles:\n" + "\n".join(failures)
    for slug in GOLDEN_SLUGS:
        assert any(s == slug for s, _ in blocks), f"no worked example for golden boat {slug}"
        assert figures[slug] >= 10, f"only {figures[slug]} checked figures for {slug}"


# --------------------------------------------------------------------------- #
# 2. document structure and code/document agreement
# --------------------------------------------------------------------------- #
def test_methodology_structure_and_thresholds_match_code():
    assert METHODOLOGY.is_file() and GLOSSARY.is_file()
    text = METHODOLOGY.read_text()

    # header
    assert "`ModelMethodologyV1`" in text.split("\n## ", 1)[0]
    head = text.split("\n## ", 1)[0]
    assert re.search(r"\| Version \| `\d+\.\d+\.\d+` \|", head), "version missing from header"
    assert re.search(r"\| Date \| \d{4}-\d{2}-\d{2} \|", head), "date missing from header"

    # one heading per component
    h2 = _headings(text, 2)
    assert len(h2) >= 8, f"expected >= 8 '## ' headings, got {len(h2)}"
    sections = _sections(text)
    for component in REQUIRED_COMPONENTS:
        assert component in sections, f"missing component section {component!r}"
        body = sections[component]
        subs = _headings(body, 3)
        for required in REQUIRED_SUBHEADINGS:
            assert any(s.startswith(required) for s in subs), (
                f"{component!r} lacks a '### {required}' subsection (has {subs})"
            )
        assert "<!-- worked-example boat=" in body, f"{component!r} has no worked-example block"
        assert "Thresholds" in body and "withheld" in body.lower()

    # required content
    assert DISCLAIMER in text
    assert "Cape 31" in text
    assert "Open decisions for Stuart" in h2

    # every threshold the document names matches the code, and is named in the doc
    for filename, name, literal in CODE_CONSTANTS:
        source = (ANALYSIS / filename).read_text()
        m = re.search(rf"^{re.escape(name)}\s*(?::[^=]+)?=\s*(\"[^\"]*\"|[^\s#]+)", source, re.MULTILINE)
        assert m, f"{filename}: constant {name} not found"
        assert m.group(1) == literal, f"{filename}: {name} is {m.group(1)}, doc assumes {literal}"
        assert name in text, f"METHODOLOGY.md does not name {name}"
        assert literal.strip('"') in text, f"METHODOLOGY.md does not state {name}'s value {literal}"
    what_if = (ANALYSIS / "what_if.py").read_text()
    assert f'ESTIMATE_DISCLAIMER = "{DISCLAIMER}"' in what_if

    # internal links resolve
    anchors = {_slug(h) for h in h2}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, f"dangling in-page link #{target}"


# --------------------------------------------------------------------------- #
# 3. glossary cross-links to docs/ai/glossary.yaml by name
# --------------------------------------------------------------------------- #
def test_glossary_cross_links_ai_glossary_by_name():
    terms = yaml.safe_load(GLOSSARY_YAML.read_text())["terms"]
    yaml_names = {t["term"] for t in terms}
    yaml_lower = {t["term"].lower() for t in terms}
    for t in terms:
        yaml_lower.update(a.lower() for a in t.get("aliases") or [])

    text = GLOSSARY.read_text()
    sections = _sections(text)
    linked_section = sections["Terms defined in docs/ai/glossary.yaml"]
    model_section = sections["Model terms not yet in docs/ai/glossary.yaml"]

    # first table column: exact yaml term names
    linked = []
    for line in linked_section.splitlines():
        if line.startswith("|") and not line.startswith("|---") and "glossary.yaml term" not in line:
            first = line.split("|")[1].strip()
            m = re.fullmatch(r"`([^`]+)`", first)
            assert m, f"first column must be a backticked yaml term name: {line!r}"
            linked.append(m.group(1))
    assert len(linked) >= 20, f"only {len(linked)} cross-linked terms"
    assert len(set(linked)) == len(linked), "duplicate rows in the cross-link table"
    missing = [t for t in linked if t not in yaml_names]
    assert not missing, f"terms not in docs/ai/glossary.yaml: {missing}"

    # core terms the methodology depends on must be cross-linked
    for core in ("TCC", "IRC", "ORC", "TWS", "Corrected time", "Elapsed time", "Time-on-time",
                 "Point", "Drift", "One-design", "Trial certificate"):
        assert core in linked, f"core term {core!r} is not cross-linked"

    # model-only terms must not shadow a yaml term (if added there, move to the table)
    model_terms = re.findall(r"^\*\*(.+?)\.\*\*", model_section, re.MULTILINE)
    assert len(model_terms) >= 10, f"only {len(model_terms)} model terms defined"
    for full in model_terms:
        base = re.sub(r"\s*\(.*?\)", "", full).strip().lower()
        assert base not in yaml_lower, f"{full!r} is now in glossary.yaml — move it to the cross-link table"

    # links into METHODOLOGY.md resolve to real headings
    anchors = {_slug(h) for h in _headings(METHODOLOGY.read_text(), 2)}
    targets = re.findall(r"METHODOLOGY\.md#([^)]+)\)", text)
    assert targets, "glossary links nowhere into METHODOLOGY.md"
    for target in targets:
        assert target in anchors, f"dangling link METHODOLOGY.md#{target}"
