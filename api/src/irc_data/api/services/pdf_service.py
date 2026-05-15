"""PDF rendering service.

Dumb renderer: takes an HTML template, injects data, runs Playwright to produce PDF.
The template is owned by the frontend — the backend just fills variables and renders.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
REPORTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "data" / "reports"


def render_pdf(engine: Engine, order_id: int) -> str | None:
    """Render a PDF for the given order. Returns the file path or None on failure."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    with engine.connect() as conn:
        order = conn.execute(
            text("""
                SELECT o.*, b.boat_name, b.sail_number,
                       COALESCE(b.design_canonical, b.design) AS design,
                       b.country,
                       t.tcc
                FROM orders o
                JOIN boats b ON b.id = o.boat_id
                LEFT JOIN LATERAL (
                    SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                    ORDER BY snapshot_date DESC LIMIT 1
                ) t ON true
                WHERE o.id = :id
            """),
            {"id": order_id},
        ).first()

    if not order or not order.report_markdown:
        logger.error(f"Order {order_id} not found or has no report content")
        return None

    # Load template
    template_path = TEMPLATE_DIR / "report.html"
    if not template_path.exists():
        logger.error(f"PDF template not found at {template_path}")
        return None

    # Render HTML from template
    html = _render_template(template_path, order)

    # Convert to PDF via Playwright
    pdf_path = REPORTS_DIR / f"{order.order_token}.pdf"
    try:
        _html_to_pdf(html, str(pdf_path))
    except Exception as e:
        logger.error(f"PDF rendering failed for order {order_id}: {e}")
        return None

    # Update order with PDF path
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE orders SET pdf_path = :path WHERE id = :id"),
            {"path": str(pdf_path), "id": order_id},
        )

    logger.info(f"PDF rendered for order {order_id}: {pdf_path}")
    return str(pdf_path)


def _render_template(template_path: Path, order) -> str:
    """Render the Jinja2 HTML template with order data."""
    import json
    from datetime import date

    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    template = env.get_template(template_path.name)

    # Convert markdown to HTML
    report_html = _markdown_to_html(order.report_markdown)

    # Parse analytics JSON
    analytics = order.report_analytics or {}
    recommendations = analytics.get("optimize", [])
    rai = analytics.get("rai")
    rivals = analytics.get("rivals", [])

    return template.render(
        boat_name=order.boat_name,
        sail_number=order.sail_number,
        design=order.design or "Unknown",
        tcc=order.tcc,
        country=order.country or "",
        report_date=date.today().strftime("%d %B %Y"),
        report_html=report_html,
        recommendations=recommendations,
        rai=rai,
        rivals=rivals,
        order_token=str(order.order_token),
    )


def _markdown_to_html(md_text: str) -> str:
    """Convert markdown to HTML. Uses a simple approach without extra deps."""
    import re

    if not md_text:
        return ""

    html = md_text

    # Headers
    html = re.sub(r"^######\s+(.+)$", r"<h6>\1</h6>", html, flags=re.MULTILINE)
    html = re.sub(r"^#####\s+(.+)$", r"<h5>\1</h5>", html, flags=re.MULTILINE)
    html = re.sub(r"^####\s+(.+)$", r"<h4>\1</h4>", html, flags=re.MULTILINE)
    html = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

    # Bold and italic
    html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

    # List items
    html = re.sub(r"^[-*]\s+(.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)

    # Wrap consecutive <li> in <ul>
    html = re.sub(r"((?:<li>.*?</li>\n?)+)", r"<ul>\1</ul>", html)

    # Paragraphs — wrap non-tag lines
    lines = html.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("<"):
            result.append(f"<p>{stripped}</p>")
        else:
            result.append(line)

    return "\n".join(result)


def _html_to_pdf(html: str, output_path: str) -> None:
    """Use Playwright to convert HTML string to PDF."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="A4",
            margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
            print_background=True,
        )
        browser.close()
