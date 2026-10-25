"""Alembic and SQLite schema lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from codeatlas.domain.errors import IndexSchemaError
from codeatlas.infrastructure.indexing.migrations import (
    current_revision,
    downgrade_database,
    upgrade_database,
)
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION, SqliteIndexStore


def database_url(path: Path) -> str:
    """Return a portable absolute SQLite URL for a temporary path."""
    return f"sqlite:///{path.as_posix()}"


@pytest.mark.asyncio
async def test_clean_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    """A clean database follows the complete reversible migration lifecycle."""
    url = database_url(tmp_path / "index.db")
    upgrade_database(url)
    assert current_revision(url) == SCHEMA_REVISION
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {
        "repositories",
        "repository_snapshots",
        "index_generations",
        "indexed_files",
        "file_versions",
        "symbols",
        "import_relations",
        "export_relations",
        "reference_relations",
        "index_jobs",
        "schema_metadata",
        "code_search_fts",
    } <= tables
    await SqliteIndexStore(url).assert_schema_current()
    downgrade_database(url)
    assert current_revision(url) is None
    upgrade_database(url)
    assert current_revision(url) == SCHEMA_REVISION


@pytest.mark.asyncio
async def test_schema_mismatch_fails_fast(tmp_path: Path) -> None:
    """The application never silently creates or migrates a stale schema."""
    url = database_url(tmp_path / "empty.db")
    with pytest.raises(IndexSchemaError):
        await SqliteIndexStore(url).assert_schema_current()


def test_foreign_keys_are_enabled_by_store(tmp_path: Path) -> None:
    """SQLite rejects generation rows for unknown repositories."""
    url = database_url(tmp_path / "foreign-key.db")
    upgrade_database(url)
    store = SqliteIndexStore(url)
    with pytest.raises(IntegrityError), store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO index_generations "
                "(generation_id,repository_id,commit_sha,generation_number,status,owner_token,"
                "created_at,updated_at) VALUES "
                "('gen_00000000000000000000000000000000','repo_00000000000000000000000000000000',"
                "'0000000000000000000000000000000000000000',1,'pending','owner','now','now')"
            )
        )
