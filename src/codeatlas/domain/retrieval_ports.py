"""Read-only ports over the immutable index generation."""

from __future__ import annotations

from typing import Protocol

from codeatlas.domain.indexing_models import IndexedFile, IndexedSymbol, SymbolId
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.domain.retrieval_models import (
    IndexedRelation,
    IndexSnapshotSummary,
    IndexTextHit,
    RelationKind,
    SymbolQuery,
)


class IndexQueryPort(Protocol):
    """Bounded queries against one active generation of the SQLite index."""

    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary | None:
        """Return active generation metadata, or None when nothing is active."""
        ...

    async def list_files(self, generation_id: str) -> tuple[IndexedFile, ...]:
        """Return generation-scoped safe file metadata including stored content."""
        ...

    async def get_file(self, generation_id: str, path: RepositoryPath) -> IndexedFile | None:
        """Return one indexed file by normalized path."""
        ...

    async def find_symbols(
        self, generation_id: str, query: SymbolQuery
    ) -> tuple[IndexedSymbol, ...]:
        """Find declarations by exact or suffix-qualified name and optional filters."""
        ...

    async def list_symbols(
        self, generation_id: str, *, path: RepositoryPath | None, limit: int
    ) -> tuple[IndexedSymbol, ...]:
        """List declarations, optionally scoped to one file."""
        ...

    async def get_symbol(self, generation_id: str, symbol_id: SymbolId) -> IndexedSymbol | None:
        """Return one declaration by opaque symbol identity."""
        ...

    async def search_text(
        self,
        generation_id: str,
        *,
        pattern: str,
        case_sensitive: bool,
        limit: int,
    ) -> tuple[IndexTextHit, ...]:
        """Search stored safe file content with a literal substring match."""
        ...

    async def find_relations(
        self,
        generation_id: str,
        *,
        relation_kind: RelationKind | None,
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        limit: int,
    ) -> tuple[IndexedRelation, ...]:
        """Return relation candidates filtered by kind, target, or source symbol."""
        ...
