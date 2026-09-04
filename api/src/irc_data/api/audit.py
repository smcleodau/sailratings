import json
import logging
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

def log_admin_action(
    engine: Engine,
    who: str,
    action: str,
    entity: str,
    pk: str,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
) -> None:
    """Log an administrative action to the admin_edits table."""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS admin_edits (
                    id          BIGSERIAL PRIMARY KEY,
                    edited_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    who         TEXT,
                    table_name  TEXT NOT NULL,
                    pk_value    TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    old_value   TEXT,
                    new_value   TEXT
                )
            """))
            conn.commit()
            
            # Check if who column exists
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='admin_edits' and column_name='who'")).fetchall()
            if not result:
                conn.execute(text("ALTER TABLE admin_edits ADD COLUMN who TEXT"))
                conn.commit()

        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO admin_edits (who, table_name, pk_value, column_name, old_value, new_value)
                    VALUES (:w, :t, :pk, :c, :ov, :nv)
                """),
                {
                    "w": who,
                    "t": entity,
                    "pk": pk,
                    "c": action,
                    "ov": json.dumps(before) if before is not None else None,
                    "nv": json.dumps(after) if after is not None else None,
                },
            )
    except Exception as e:
        print(f"FAILED TO LOG: {e}")
        logger.error(f"Failed to log admin action: {e}", exc_info=True)
