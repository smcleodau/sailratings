"""End-to-end test: order #21 + REPORT_V2=true → 11-section PDF."""
import os
import pytest
from pathlib import Path
from sqlalchemy import text
from irc_data.db.connection import get_engine


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="no API key")
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
