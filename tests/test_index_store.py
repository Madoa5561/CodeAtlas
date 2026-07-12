"""Transactional SQLite generation and recovery tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codeatlas.domain.errors import IndexBusyError
from codeatlas.domain.indexing_models import IndexGenerationStatus
from codeatlas.domain.repository_models import RepositoryId
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.infrastructure.indexing.store import SqliteIndexStore


def make_store(tmp_path: Path) -> SqliteIndexStore:
    """Create a migrated isolated store."""
    url = f"sqlite:///{(tmp_path / 'index.db').as_posix()}"
    upgrade_database(url)
    return SqliteIndexStore(url)


@pytest.mark.asyncio
async def test_atomic_activation_supersede_and_rollback(tmp_path: Path) -> None:
    """Only validated generations become active and rollback is atomic."""
    store = make_store(tmp_path)
    repository_id = RepositoryId("repo_" + "1" * 32)
    await store.acquire_lease(repository_id, "owner-one", datetime.now(UTC) + timedelta(minutes=1))
    first = await store.create_generation(repository_id, "a" * 40, "parser", "policy", "owner-one")
    await store.set_status(first.generation_id, IndexGenerationStatus.RUNNING)
    await store.set_status(first.generation_id, IndexGenerationStatus.VALIDATING)
    valid, details = await store.validate_generation(first.generation_id, 0.0)
    assert valid and details == ("validation passed",)
    await store.save_validation(first.generation_id, valid, details)
    await store.activate(first.generation_id)
    await store.release_lease(repository_id, "owner-one")
    assert await store.get_active(repository_id) == first
    with pytest.raises(ValueError, match="invalid generation transition"):
        await store.set_status(first.generation_id, IndexGenerationStatus.RUNNING)

    await store.acquire_lease(repository_id, "owner-two", datetime.now(UTC) + timedelta(minutes=1))
    second = await store.create_generation(repository_id, "b" * 40, "parser", "policy", "owner-two")
    await store.set_status(second.generation_id, IndexGenerationStatus.RUNNING)
    await store.set_status(second.generation_id, IndexGenerationStatus.VALIDATING)
    await store.save_validation(second.generation_id, True, ("validation passed",))
    await store.activate(second.generation_id)
    assert await store.get_status(first.generation_id) is IndexGenerationStatus.SUPERSEDED
    assert await store.get_active(repository_id) == second
    await store.rollback(repository_id, first.generation_id)
    assert await store.get_active(repository_id) == first
    assert await store.get_status(second.generation_id) is IndexGenerationStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_lease_conflict_and_stale_recovery(tmp_path: Path) -> None:
    """Repository leases reject concurrency and stale jobs fail only staging generations."""
    store = make_store(tmp_path)
    repository_id = RepositoryId("repo_" + "2" * 32)
    await store.acquire_lease(repository_id, "current", datetime.now(UTC) + timedelta(minutes=1))
    with pytest.raises(IndexBusyError):
        await store.acquire_lease(repository_id, "other", datetime.now(UTC) + timedelta(minutes=1))
    await store.release_lease(repository_id, "current")
    await store.acquire_lease(repository_id, "stale", datetime.now(UTC) - timedelta(minutes=1))
    generation = await store.create_generation(repository_id, "c" * 40, "parser", "policy", "stale")
    await store.set_status(generation.generation_id, IndexGenerationStatus.RUNNING)
    assert await store.mark_stale_failed(datetime.now(UTC)) == 1
    assert await store.get_status(generation.generation_id) is IndexGenerationStatus.FAILED
