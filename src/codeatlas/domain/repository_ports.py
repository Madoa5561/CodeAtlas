"""Repository ports implemented by read-only provider adapters."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from codeatlas.domain.repository_models import (
    BlameEntry,
    CommitInfo,
    DiffResult,
    FileContent,
    FileHistoryEntry,
    RepositoryDescriptor,
    RepositoryEntry,
    RepositoryId,
    RepositoryLocation,
    RepositoryPath,
    ResolvedRevision,
    RevisionRef,
    TextSearchHit,
    TextSearchQuery,
)


class RepositoryRegistryPort(Protocol):
    """Exchangeable registry for canonical repository descriptors."""

    def add(self, descriptor: RepositoryDescriptor, canonical_key: str) -> RepositoryDescriptor:
        """Add or return an existing descriptor for a canonical identity."""
        ...

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        """Return a registered descriptor."""
        ...

    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        """Return every registered descriptor in stable repository-ID order."""
        ...


class RepositoryProviderPort(Protocol):
    """Typed read-only operations available to application services."""

    async def register_local_repository(self, location: RepositoryLocation) -> RepositoryDescriptor:
        """Register one canonical non-bare local repository."""
        ...

    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        """Return one previously registered repository."""
        ...

    async def resolve_revision(
        self, repository_id: RepositoryId, revision: RevisionRef
    ) -> ResolvedRevision:
        """Resolve a ref to an immutable full commit SHA."""
        ...

    async def list_branches(self, repository_id: RepositoryId) -> tuple[ResolvedRevision, ...]:
        """List branches resolved to commits."""
        ...

    async def list_tags(self, repository_id: RepositoryId) -> tuple[ResolvedRevision, ...]:
        """List tags peeled and resolved to commits."""
        ...

    async def list_directory(
        self, revision: ResolvedRevision, path: RepositoryPath
    ) -> tuple[RepositoryEntry, ...]:
        """List one bounded commit tree directory."""
        ...

    async def read_file(self, revision: ResolvedRevision, path: RepositoryPath) -> FileContent:
        """Read one validated text blob from a commit."""
        ...

    async def search_text(
        self, revision: ResolvedRevision, query: TextSearchQuery
    ) -> tuple[TextSearchHit, ...]:
        """Search eligible files from a SHA-pinned snapshot."""
        ...

    async def get_commit(self, revision: ResolvedRevision) -> CommitInfo:
        """Return metadata for one commit."""
        ...

    async def search_commits(
        self,
        repository_id: RepositoryId,
        query: str,
        *,
        revision: RevisionRef,
        author: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        path: RepositoryPath | None = None,
        max_results: int = 100,
    ) -> tuple[CommitInfo, ...]:
        """Search bounded commit metadata with optional filters."""
        ...

    async def get_file_history(
        self, revision: ResolvedRevision, path: RepositoryPath, *, max_results: int = 100
    ) -> tuple[FileHistoryEntry, ...]:
        """Return bounded history for one path."""
        ...

    async def get_blame(
        self, revision: ResolvedRevision, path: RepositoryPath
    ) -> tuple[BlameEntry, ...]:
        """Return commit-pinned blame ranges."""
        ...

    async def compare_refs(self, base: ResolvedRevision, head: ResolvedRevision) -> DiffResult:
        """Return a bounded safe diff between revisions."""
        ...

    async def get_diff(self, revision: ResolvedRevision) -> DiffResult:
        """Return a bounded safe commit diff."""
        ...


class LocalRepositoryRegistrationPort(Protocol):
    """Application-facing registration use case boundary."""

    async def register(self, path: Path) -> RepositoryDescriptor:
        """Register a local repository from an absolute or relative path."""
        ...
