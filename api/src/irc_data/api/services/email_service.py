"""Email delivery service.

Sends report PDFs via Resend API. The email HTML template is frontend-owned.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.env import FRONTEND_URL

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def send_report_email(engine: Engine, order_id: int) -> bool:
    """Send the report email with PDF attachment. Returns True on success."""
    import resend

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not configured")
        return False

    resend.api_key = api_key

    with engine.connect() as conn:
        order = conn.execute(
            text("""
                SELECT o.*, b.boat_name, b.sail_number
                FROM orders o
                JOIN boats b ON b.id = o.boat_id
                WHERE o.id = :id
            """),
            {"id": order_id},
        ).first()

    if not order:
        logger.error(f"Order {order_id} not found")
        return False

    if not order.email:
        logger.info(f"Order {order_id} has no email — skipping send")
        return False

    # Build email HTML
    email_html = _build_email_html(order)

    # Prepare attachment
    attachments = []
    if order.pdf_path and Path(order.pdf_path).exists():
        pdf_data = Path(order.pdf_path).read_bytes()
        import base64

        attachments.append(
            {
                "filename": f"{order.boat_name} - IRC Rating Report.pdf",
                "content": base64.b64encode(pdf_data).decode(),
                "type": "application/pdf",
            }
        )

    from irc_data.api.services.analytics_service import track
    try:
        params = {
            "from": "SailRatings <reports@sailratings.com>",
            "to": [order.email],
            "subject": f"Your IRC Rating Report — {order.boat_name}",
            "html": email_html,
        }
        if attachments:
            params["attachments"] = attachments

        resend.Emails.send(params)
    except Exception as e:
        logger.error(f"Failed to send email for order {order_id}: {e}")
        track("report_email_failed", str(order.order_token), {
            "order_id": order_id,
            "boat_id": order.boat_id,
            "email": order.email,
            "error": str(e)[:200],
        })
        return False

    # Mark email sent
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE orders SET email_sent_at = :now WHERE id = :id"),
            {"now": datetime.now(timezone.utc), "id": order_id},
        )

    track("report_email_sent", str(order.order_token), {
        "order_id": order_id,
        "boat_id": order.boat_id,
        "email": order.email,
        "has_pdf": bool(attachments),
    })
    logger.info(f"Report email sent for order {order_id} to {order.email}")
    return True


def _build_email_html(order) -> str:
    """Render the email template."""
    template_path = TEMPLATE_DIR / "email_report.html"

    if template_path.exists():
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader(str(template_path.parent)))
        template = env.get_template(template_path.name)
        return template.render(
            boat_name=order.boat_name,
            sail_number=order.sail_number,
            order_token=str(order.order_token),
            report_url=f"{FRONTEND_URL}/report/{order.order_token}",
        )

    # Fallback inline template
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
        <h1 style="color: #1a365d; font-size: 24px; margin-bottom: 8px;">Your IRC Rating Report</h1>
        <h2 style="color: #4a5568; font-size: 18px; font-weight: normal; margin-top: 0;">{order.boat_name} ({order.sail_number})</h2>
        <hr style="border: none; border-top: 2px solid #2b6cb0; margin: 24px 0;">
        <p style="color: #2d3748; line-height: 1.6;">Your full IRC rating analysis is ready. The PDF is attached to this email, and you can also view the interactive report online:</p>
        <p style="text-align: center; margin: 32px 0;">
            <a href="{FRONTEND_URL}/report/{order.order_token}"
               style="background: #2b6cb0; color: white; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                View Report Online
            </a>
        </p>
        <p style="color: #718096; font-size: 14px; margin-top: 40px;">
            Questions about your report? Reply to this email.<br>
            &copy; SailRatings.com
        </p>
    </div>
    """
