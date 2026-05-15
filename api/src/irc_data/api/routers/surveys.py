"""Survey endpoints for post-report feedback."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/surveys", tags=["Surveys"])


class SurveySubmission(BaseModel):
    order_token: str
    usefulness_score: int | None = None
    newsletter_signup: bool = False
    user_type: str | None = None
    missing_info: str | None = None
    email: str | None = None


@router.post("/submit")
def submit_survey(
    body: SurveySubmission,
    engine: Engine = Depends(get_db),
):
    """Submit a post-report survey response."""
    # Validate score range
    if body.usefulness_score is not None and not (1 <= body.usefulness_score <= 10):
        raise HTTPException(status_code=422, detail="usefulness_score must be 1-10")

    # Verify the order exists
    with engine.connect() as conn:
        order = conn.execute(
            text("SELECT id FROM orders WHERE order_token = :token"),
            {"token": body.order_token},
        ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Check for duplicate submission
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT id FROM survey_responses WHERE order_token = :token::uuid"),
            {"token": body.order_token},
        ).first()

    if existing:
        raise HTTPException(status_code=409, detail="Survey already submitted for this order")

    # Insert survey response
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO survey_responses
                    (order_token, usefulness_score, newsletter_signup, user_type, missing_info, email)
                VALUES
                    (:token::uuid, :score, :newsletter, :user_type, :missing_info, :email)
            """),
            {
                "token": body.order_token,
                "score": body.usefulness_score,
                "newsletter": body.newsletter_signup,
                "user_type": body.user_type,
                "missing_info": body.missing_info,
                "email": body.email,
            },
        )

    logger.info(f"Survey submitted for order {body.order_token}")
    return {"status": "ok", "message": "Thank you for your feedback!"}
