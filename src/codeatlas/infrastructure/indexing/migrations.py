"""Explicit Alembic management API used by the administrative CLI and tests."""

from __future__ import annotations

from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine


def alembic_config(database_url: str) -> Config:
    """Build an Alembic configuration without reading application secrets."""
    config = Config()
    migration_root = files("codeatlas.migrations")
    config.set_main_option("script_location", str(migration_root))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str, revision: str = "head") -> None:
    """Upgrade an explicitly selected database to a named revision."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade_database(database_url: str, revision: str = "base") -> None:
    """Downgrade an explicitly selected database to a named revision."""
    command.downgrade(alembic_config(database_url), revision)


def current_revision(database_url: str) -> str | None:
    """Return the current database revision without applying migrations."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
