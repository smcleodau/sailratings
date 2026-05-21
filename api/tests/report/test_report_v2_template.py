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
