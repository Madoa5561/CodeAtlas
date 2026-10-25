"""Vector projection rebuild and query services."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.domain.retrieval_ports import IndexQueryPort
from codeatlas.domain.vector_models import VectorPoint, VectorSearchHit
from codeatlas.domain.vector_ports import EmbeddingPort, VectorIndexPort

_NAMESPACE = uuid.UUID("4e7f0f8a-2d4a-4f63-9f3f-3f5e4a8f2c11")


@dataclass(frozen=True, slots=True)
class VectorProjectionStats:
    """Observable result of one projection rebuild."""

    repository_id: RepositoryId
    generation_id: str
    files: int
    chunks: int
    points: int
    seconds: float


@dataclass(slots=True)
class VectorProjector:
    """Build a generation-scoped Qdrant projection from the SQLite index."""

    source: IndexQueryPort
    embedding: EmbeddingPort
    index: VectorIndexPort
    chunk_lines: int = 40
    overlap_lines: int = 8
    max_chunks_per_file: int = 50
    max_chars: int = 4_000
    embedding_batch: int = 16

    async def rebuild(self, repository_id: RepositoryId) -> VectorProjectionStats:
        """Replace all vector points for the active repository generation."""
        started = time.monotonic()
        snapshot = await self.source.get_active_snapshot(repository_id)
        if snapshot is None:
            raise ValueError("no active index generation is available")
        files = await self.source.list_files(snapshot.generation_id)
        await self.index.ensure_collection()
        await self.index.delete_generation(snapshot.generation_id)

        chunks: list[tuple[str, int, int, str]] = []
        for file in files:
            if not file.content or file.path.is_root:
                continue
            chunks.extend(self._chunks(file.path.as_posix(), file.content))
        points: list[VectorPoint] = []
        for start in range(0, len(chunks), self.embedding_batch):
            batch = chunks[start : start + self.embedding_batch]
            texts = [chunk[3] for chunk in batch]
            vectors = await self.embedding.embed(texts)
            if len(vectors) != len(batch):
                raise ValueError("embedding provider returned the wrong batch size")
            for (path, start_line, end_line, excerpt), vector in zip(batch, vectors, strict=True):
                point_id = uuid.uuid5(
                    _NAMESPACE,
                    f"{snapshot.generation_id}:{path}:{start_line}:{end_line}",
                )
                points.append(
                    VectorPoint(
                        point_id=str(point_id),
                        vector=tuple(vector),
                        repository_id=repository_id,
                        generation_id=snapshot.generation_id,
                        commit_sha=snapshot.commit_sha,
                        path=RepositoryPath(PurePosixPath(path)),
                        start_line=start_line,
                        end_line=end_line,
                        excerpt=excerpt,
                    )
                )
        if points:
            await self.index.upsert(points)
        return VectorProjectionStats(
            repository_id=repository_id,
            generation_id=snapshot.generation_id,
            files=len(files),
            chunks=len(chunks),
            points=len(points),
            seconds=time.monotonic() - started,
        )

    def _chunks(self, path: str, content: str) -> list[tuple[str, int, int, str]]:
        lines = content.splitlines()
        if not lines:
            return []
        if len(lines) <= self.chunk_lines:
            excerpt = "\n".join(lines)[: self.max_chars]
            if not excerpt.strip():
                return []
            return [(path, 1, len(lines), excerpt)]
        step = max(1, self.chunk_lines - self.overlap_lines)
        chunks: list[tuple[str, int, int, str]] = []
        for start in range(0, len(lines), step):
            end = min(len(lines), start + self.chunk_lines)
            excerpt = "\n".join(lines[start:end])[: self.max_chars]
            if excerpt.strip():
                chunks.append((path, start + 1, end, excerpt))
            if len(chunks) >= self.max_chunks_per_file:
                break
        return chunks


@dataclass(slots=True)
class VectorSearchService:
    """Embed a bounded query and search the active vector projection."""

    embedding: EmbeddingPort
    index: VectorIndexPort

    async def search(
        self,
        repository_id: RepositoryId,
        generation_id: str,
        query: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        vectors = await self.embedding.embed([query])
        if len(vectors) != 1:
            raise ValueError("embedding provider returned an unexpected result")
        return await self.index.search(
            vectors[0],
            repository_id=repository_id,
            generation_id=generation_id,
            limit=limit,
        )
