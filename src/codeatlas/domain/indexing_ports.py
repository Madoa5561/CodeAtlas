"""Ports for parser selection and transactional index persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from codeatlas.domain.indexing_models import (
    ExportRelation,
    ImportRelation,
    IndexedFile,
    IndexedSymbol,
    IndexGenerationId,
    IndexGenerationStatus,
    IndexingFailure,
    IndexingStatistics,
    ParsedFile,
    ReferenceRelation,
    RepositorySnapshot,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath


class SourceParserPort(Protocol):
    """Language parser selected without exposing Tree-sitter types."""

    @property
    def parser_id(self) -> str: ...

    @property
    def parser_version(self) -> str: ...

    @property
    def grammar_version(self) -> str: ...

    @property
    def language_ids(self) -> tuple[str, ...]: ...

    @property
    def supported_extensions(self) -> tuple[str, ...]: ...

    def supports(self, path: RepositoryPath) -> bool:
        """Return whether this parser accepts the path."""
        ...

    def parse(
        self,
        snapshot: RepositorySnapshot,
        file: IndexedFile,
    ) -> ParsedFile:
        """Parse one already validated bounded source file."""
        ...


class IndexStorePort(Protocol):
    """Transactional persistence boundary used by application indexing services."""

    async def assert_schema_current(self) -> None: ...
    async def acquire_lease(
        self, repository_id: RepositoryId, owner_token: str, expires_at: datetime
    ) -> None: ...
    async def release_lease(self, repository_id: RepositoryId, owner_token: str) -> None: ...
    async def create_generation(
        self,
        repository_id: RepositoryId,
        commit_sha: str,
        parser_fingerprint: str,
        policy_fingerprint: str,
        owner_token: str,
        parser_metadata: tuple[str, ...] = (),
    ) -> RepositorySnapshot: ...
    async def set_status(
        self, generation_id: IndexGenerationId, target: IndexGenerationStatus
    ) -> None: ...
    async def add_file_bundle(
        self,
        file: IndexedFile,
        symbols: tuple[IndexedSymbol, ...],
        imports: tuple[ImportRelation, ...],
        exports: tuple[ExportRelation, ...],
        references: tuple[ReferenceRelation, ...],
    ) -> None: ...
    async def copy_file_bundle(
        self,
        source_generation_id: IndexGenerationId,
        target_generation_id: IndexGenerationId,
        path: RepositoryPath,
        commit_sha: str,
    ) -> bool: ...
    async def list_files(self, generation_id: IndexGenerationId) -> tuple[IndexedFile, ...]: ...
    async def get_active(self, repository_id: RepositoryId) -> RepositorySnapshot | None: ...
    async def get_generation(self, generation_id: IndexGenerationId) -> RepositorySnapshot: ...
    async def get_status(self, generation_id: IndexGenerationId) -> IndexGenerationStatus: ...
    async def list_generations(
        self, repository_id: RepositoryId
    ) -> tuple[RepositorySnapshot, ...]: ...
    async def save_validation(
        self, generation_id: IndexGenerationId, valid: bool, details: tuple[str, ...]
    ) -> None: ...
    async def validate_generation(
        self, generation_id: IndexGenerationId, maximum_failure_rate: float
    ) -> tuple[bool, tuple[str, ...]]: ...
    async def activate(self, generation_id: IndexGenerationId) -> None: ...
    async def fail_generation(
        self,
        generation_id: IndexGenerationId,
        failure: IndexingFailure,
        statistics: IndexingStatistics,
    ) -> None: ...
    async def save_statistics(
        self, generation_id: IndexGenerationId, statistics: IndexingStatistics
    ) -> None: ...
    async def mark_stale_failed(self, stale_before: datetime) -> int: ...
    async def rollback(
        self, repository_id: RepositoryId, generation_id: IndexGenerationId
    ) -> None: ...
