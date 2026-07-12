"""End-to-end full and incremental indexing over real temporary Git repositories."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import GitRepositoryFixture, run_git
from sqlalchemy import func, select

from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.application.indexing import IndexingPolicy, TransactionalIndexingService
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.errors import IndexingError, IndexValidationError
from codeatlas.domain.indexing_models import IndexGenerationStatus, IndexingRequest
from codeatlas.domain.indexing_ports import SourceParserPort
from codeatlas.domain.repository_models import RepositoryLocation
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.infrastructure.indexing.models import FileVersionRow, IndexedFileRow, SymbolRow
from codeatlas.infrastructure.indexing.store import SqliteIndexStore


def make_service(
    tmp_path: Path,
    *,
    parser: SourceParserPort | None = None,
    max_failure_rate: float = 0.05,
) -> tuple[TransactionalIndexingService, SqliteIndexStore]:
    """Create an M2 provider and migrated M3 store with no external services."""
    repository = LocalGitRepositoryPlugin.from_settings(RepositorySettings()).provider
    url = f"sqlite:///{(tmp_path / 'index.db').as_posix()}"
    if not (tmp_path / "index.db").exists():
        upgrade_database(url)
    store = SqliteIndexStore(url)
    service = TransactionalIndexingService(
        repository,
        store,
        (parser or TreeSitterTypeScriptParser(),),
        IndexingPolicy(max_failure_rate=max_failure_rate),
    )
    return service, store


@pytest.mark.asyncio
async def test_full_and_incremental_are_commit_pinned_and_equivalent(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    """Incremental add/modify/delete and reuse produce the same file set as a full index."""
    (git_repository.root / "main.ts").write_text(
        "export class Service { run(): number { return helper(); } }\n"
        "export function helper() { return 1; }\n",
        encoding="utf-8",
    )
    (git_repository.root / "old.ts").write_text("export const old = 1;\n", encoding="utf-8")
    run_git(git_repository.root, "add", "main.ts", "old.ts")
    run_git(git_repository.root, "commit", "-m", "add index fixtures")

    service, store = make_service(tmp_path)
    descriptor = await service.repository.register_local_repository(
        RepositoryLocation(git_repository.root)
    )
    first = await service.create_index(
        IndexingRequest(descriptor.repository_id, "main", incremental=False)
    )
    assert first.status is IndexGenerationStatus.ACTIVE
    first_files = await store.list_files(first.snapshot.generation_id)
    indexed_main = next(item for item in first_files if item.path.as_posix() == "main.ts")
    assert "Service" in (indexed_main.content or "")
    committed_main = indexed_main.content

    (git_repository.root / "main.ts").write_text(
        "export class Service { run(): number { return 2; } }\n", encoding="utf-8"
    )
    (git_repository.root / "new.ts").write_text("export interface Added {}\n", encoding="utf-8")
    (git_repository.root / "old.ts").unlink()
    run_git(git_repository.root, "add", "-A", "--", "main.ts", "new.ts", "old.ts")
    run_git(git_repository.root, "commit", "-m", "update index fixtures")
    (git_repository.root / "main.ts").write_text("working tree only secret\n", encoding="utf-8")

    second = await service.create_index(
        IndexingRequest(descriptor.repository_id, "main", incremental=True)
    )
    assert second.statistics.reused > 0
    assert await store.get_status(first.snapshot.generation_id) is IndexGenerationStatus.SUPERSEDED
    incremental_files = await store.list_files(second.snapshot.generation_id)
    paths = {item.path.as_posix() for item in incremental_files}
    assert "new.ts" in paths and "old.ts" not in paths
    second_main = next(item for item in incremental_files if item.path.as_posix() == "main.ts")
    assert "return 2" in (second_main.content or "")
    assert "working tree only" not in (second_main.content or "")
    secret = next(item for item in incremental_files if item.path.as_posix() == ".env")
    assert secret.content is None
    assert any(item.status_reason == "symlink" for item in incremental_files)
    assert any(item.status_reason == "submodule" for item in incremental_files)
    assert second.snapshot.parser_metadata
    with store._sessions() as session:
        contents = session.scalars(
            select(FileVersionRow.content_utf8).where(FileVersionRow.content_utf8.is_not(None))
        ).all()
        assert all(b"not-indexed" not in content for content in contents if content is not None)

    third = await service.create_index(
        IndexingRequest(descriptor.repository_id, "main", incremental=False)
    )
    full_files = await store.list_files(third.snapshot.generation_id)
    assert {
        (item.path.as_posix(), item.content, item.security_status) for item in incremental_files
    } == {(item.path.as_posix(), item.content, item.security_status) for item in full_files}
    assert committed_main != second_main.content
    with store._sessions() as session:
        incremental_symbols = session.scalar(
            select(func.count())
            .select_from(SymbolRow)
            .where(SymbolRow.generation_id == second.snapshot.generation_id.value)
        )
        full_symbols = session.scalar(
            select(func.count())
            .select_from(SymbolRow)
            .where(SymbolRow.generation_id == third.snapshot.generation_id.value)
        )
        assert incremental_symbols == full_symbols


class FailingParser:
    """Deterministic file-level parser failure used to verify publication isolation."""

    parser_id = "test.failing-parser"
    parser_version = "2.0.0"
    grammar_version = "test-2"
    language_ids = ("typescript",)
    supported_extensions = (".ts",)

    def supports(self, path: object) -> bool:
        return str(getattr(path, "value", path)).endswith(".ts")

    def parse(self, snapshot: object, file: object) -> object:
        raise IndexingError("Parser fixture failed.")


@pytest.mark.asyncio
async def test_failed_staging_preserves_old_active(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    """A validation failure marks only staging failed and leaves prior active readable."""
    (git_repository.root / "failure.ts").write_text("export const ok = 1;\n", encoding="utf-8")
    run_git(git_repository.root, "add", "failure.ts")
    run_git(git_repository.root, "commit", "-m", "add failure fixture")
    service, store = make_service(tmp_path)
    descriptor = await service.repository.register_local_repository(
        RepositoryLocation(git_repository.root)
    )
    active = await service.create_index(IndexingRequest(descriptor.repository_id, "main"))

    failing_service = TransactionalIndexingService(
        service.repository,
        store,
        (FailingParser(),),  # type: ignore[arg-type]
        IndexingPolicy(max_failure_rate=0.0),
    )
    with pytest.raises(IndexValidationError):
        await failing_service.create_index(
            IndexingRequest(descriptor.repository_id, "main", incremental=True)
        )
    assert await store.get_active(descriptor.repository_id) == active.snapshot
    generations = await store.list_generations(descriptor.repository_id)
    assert await store.get_status(generations[-1].generation_id) is IndexGenerationStatus.FAILED


def test_safe_database_never_contains_secret_fixture(tmp_path: Path) -> None:
    """A direct schema query has no unsafe content before indexing."""
    _, store = make_service(tmp_path)
    with store._sessions() as session:
        assert session.scalar(select(func.count()).select_from(FileVersionRow)) == 0
        assert session.scalar(select(func.count()).select_from(IndexedFileRow)) == 0


@pytest.mark.asyncio
async def test_cancellation_marks_staging_failed_and_releases_lease(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation cannot publish partial data or retain the repository lease."""
    service, store = make_service(tmp_path)
    descriptor = await service.repository.register_local_repository(
        RepositoryLocation(git_repository.root)
    )
    entered = asyncio.Event()

    async def block_build(*_arguments: object) -> object:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(service, "_build", block_build)
    task = asyncio.create_task(
        service.create_index(IndexingRequest(descriptor.repository_id, "main"))
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    generations = await store.list_generations(descriptor.repository_id)
    assert await store.get_status(generations[-1].generation_id) is IndexGenerationStatus.FAILED
    await store.acquire_lease(
        descriptor.repository_id,
        "after-cancel",
        datetime.now(UTC) + timedelta(minutes=1),
    )
    await store.release_lease(descriptor.repository_id, "after-cancel")
