"""Dependency injection for FastAPI routes."""

from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine


def get_db() -> Engine:
    """Return the shared SQLAlchemy engine."""
    return get_engine()
