import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the target database URL.  Precedence (DP-03-05):
#   1. An *explicitly set* ``sqlalchemy.url`` that differs from the value
#      baked into ``alembic.ini`` — e.g. the migration-verification harness
#      or ``irc-data db-upgrade`` targeting a throwaway DB.  This wins so a
#      stale ``DATABASE_URL`` in the environment can never silently redirect
#      a migration at the wrong (e.g. production) database.
#   2. ``IRC_DATABASE_URL`` / ``DATABASE_URL`` environment variables.
#   3. The ``sqlalchemy.url`` baked into ``alembic.ini``.
import configparser  # noqa: E402


def _ini_default_url() -> str | None:
    """Read the default sqlalchemy.url straight from alembic.ini."""
    if config.config_file_name is None:
        return None
    parser = configparser.ConfigParser()
    parser.read(config.config_file_name)
    return parser.get("alembic", "sqlalchemy.url", fallback=None)


_current_url = config.get_main_option("sqlalchemy.url")
_ini_default = _ini_default_url()
_env_url = os.environ.get("IRC_DATABASE_URL") or os.environ.get("DATABASE_URL")

if _current_url and _ini_default is not None and _current_url != _ini_default:
    # A programmatically-set URL (not just the ini default) — honour it.
    pass
elif _env_url:
    db_url = _env_url
    # Railway gives postgresql:// but SQLAlchemy needs postgresql+psycopg://
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config.set_main_option("sqlalchemy.url", db_url)

# Import models so Alembic can see them for autogenerate
from irc_data.db.models import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
