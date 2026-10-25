"""Ports for embeddings and vector projections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from codeatlas.domain.repository_models import RepositoryId
from codeatlas.domain.vector_models import VectorPoint, VectorSearchHit


class EmbeddingPort(Protocol):
    """Deterministic text-to-vector provider surface."""

    @property
    def dimensions(self) -> int: ...

    @property
    def model_id(self) -> str: ...

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...

    async def aclose(self) -> None: ...


class VectorIndexPort(Protocol):
    """Generation-scoped vector collection maintained outside SQLite."""

    @property
    def dimensions(self) -> int: ...

    async def ensure_collection(self) -> None: ...

    async def upsert(self, points: Sequence[VectorPoint]) -> None: ...

    async def search(
        self,
        vector: Sequence[float],
        *,
        repository_id: RepositoryId,
        generation_id: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]: ...

    async def count(
        self,
        *,
        repository_id: RepositoryId,
        generation_id: str,
    ) -> int: ...

    async def delete_generation(self, generation_id: str) -> None: ...

    async def aclose(self) -> None: ...


class VectorQueryPort(Protocol):
    """Embed a query and search the active projection."""

    async def search(
        self,
        repository_id: RepositoryId,
        generation_id: str,
        query: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]: ...
