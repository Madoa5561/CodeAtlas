"""Embedding, Qdrant projection, and vector search tool tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx
import pytest

from codeatlas.adapters.embedding.local import LocalHashEmbedding
from codeatlas.adapters.embedding.openai_compatible import OpenAICompatibleEmbedding
from codeatlas.adapters.vector.qdrant import QdrantVectorIndex
from codeatlas.application.tools.index_tools import search_vector
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import SearchVectorInput
from codeatlas.application.vector_projection import VectorProjector
from codeatlas.config.settings import EmbeddingSettings, QdrantSettings
from codeatlas.domain.agent_models import ToolContext
from codeatlas.domain.errors import EmbeddingProviderError
from codeatlas.domain.indexing_models import IndexGenerationStatus
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
    RepositoryPath,
)
from codeatlas.domain.retrieval_models import IndexSnapshotSummary
from codeatlas.domain.vector_models import VectorPoint, VectorSearchHit

_REPO = RepositoryId("repo_" + "a" * 32)
_SHA = "b" * 40
_NOW = datetime(2026, 10, 25, tzinfo=UTC)


@pytest.mark.asyncio
async def test_local_hash_embedding_is_deterministic_and_normalized() -> None:
    provider = LocalHashEmbedding(EmbeddingSettings(enabled=True, provider="local", dimensions=32))
    first, second = await provider.embed(["Client login", "Client login"])
    assert len(first) == 32
    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9
    await provider.aclose()


@pytest.mark.asyncio
async def test_openai_embedding_maps_batches_and_rejects_dimension_mismatch() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]}
                    for index, _ in enumerate(body["input"])
                ]
            },
        )

    provider = OpenAICompatibleEmbedding(
        EmbeddingSettings(
            enabled=True,
            provider="openai-compatible",
            api_key="key",
            dimensions=8,
            batch_size=2,
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        vectors = await provider.embed(["one", "two", "three"])
    finally:
        await provider.aclose()
    assert len(vectors) == 3
    assert len(requests) == 2
    assert vectors[0] == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)

    bad = OpenAICompatibleEmbedding(
        EmbeddingSettings(
            enabled=True,
            provider="openai-compatible",
            api_key="key",
            dimensions=8,
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        ),
    )
    try:
        with pytest.raises(EmbeddingProviderError):
            await bad.embed(["one"])
    finally:
        await bad.aclose()


@pytest.mark.asyncio
async def test_qdrant_adapter_ensures_upserts_searches_and_filters() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/points/search"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "score": 0.91,
                            "payload": {
                                "repository_id": _REPO.value,
                                "generation_id": "gen_" + "c" * 32,
                                "commit_sha": _SHA,
                                "path": "src/client.ts",
                                "start_line": 4,
                                "end_line": 8,
                                "excerpt": "export class Client {}",
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith("/points/count"):
            return httpx.Response(200, json={"result": {"count": 3}})
        return httpx.Response(200, json={"result": {"status": "ok"}})

    settings = QdrantSettings(enabled=True, url="http://qdrant:6333")
    index = QdrantVectorIndex(settings, dimensions=4, transport=httpx.MockTransport(handler))
    point = VectorPoint(
        point_id="123e4567-e89b-12d3-a456-426614174000",
        vector=(0.1, 0.2, 0.3, 0.4),
        repository_id=_REPO,
        generation_id="gen_" + "c" * 32,
        commit_sha=_SHA,
        path=RepositoryPath(PurePosixPath("src/client.ts")),
        start_line=4,
        end_line=8,
        excerpt="export class Client {}",
    )
    try:
        await index.ensure_collection()
        await index.upsert([point])
        hits = await index.search(
            (0.1, 0.2, 0.3, 0.4),
            repository_id=_REPO,
            generation_id="gen_" + "c" * 32,
            limit=5,
        )
        count = await index.count(repository_id=_REPO, generation_id="gen_" + "c" * 32)
        await index.delete_generation("gen_" + "c" * 32)
    finally:
        await index.aclose()

    assert count == 3
    assert hits[0].path.as_posix() == "src/client.ts"
    search_call = next(call for call in calls if call[1].endswith("/points/search"))
    assert search_call[2] is not None
    assert search_call[2]["filter"] == {
        "must": [
            {"key": "repository_id", "match": {"value": _REPO.value}},
            {"key": "generation_id", "match": {"value": "gen_" + "c" * 32}},
        ]
    }


class FakeEmbedding:
    dimensions = 4
    model_id = "fake"

    async def embed(self, texts: list[str] | tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(len(text) % 7), 0.5, 0.25, 0.125) for text in texts)

    async def aclose(self) -> None:
        return None


class FakeVectorIndex:
    dimensions = 4

    def __init__(self) -> None:
        self.points: list[VectorPoint] = []
        self.deleted: list[str] = []
        self.ensured = 0

    async def ensure_collection(self) -> None:
        self.ensured += 1

    async def upsert(self, points: list[VectorPoint] | tuple[VectorPoint, ...]) -> None:
        self.points.extend(points)

    async def search(
        self,
        vector: list[float] | tuple[float, ...],
        *,
        repository_id: RepositoryId,
        generation_id: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        return ()

    async def count(self, *, repository_id: RepositoryId, generation_id: str) -> int:
        return len(self.points)

    async def delete_generation(self, generation_id: str) -> None:
        self.deleted.append(generation_id)

    async def aclose(self) -> None:
        return None


class FakeSource:
    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary:
        return IndexSnapshotSummary(
            repository_id=repository_id,
            commit_sha=_SHA,
            generation_id="gen_" + "c" * 32,
            generation_number=1,
            status=IndexGenerationStatus.ACTIVE,
        )

    async def list_files(self, generation_id: str) -> tuple[object, ...]:
        return (
            type(
                "File",
                (),
                {
                    "path": RepositoryPath(PurePosixPath("src/client.ts")),
                    "content": "\n".join(f"line {index}" for index in range(1, 90)),
                },
            )(),
            type(
                "File",
                (),
                {
                    "path": RepositoryPath(PurePosixPath("README.md")),
                    "content": "# CodeAtlas",
                },
            )(),
        )


@pytest.mark.asyncio
async def test_projector_chunks_embeds_and_upserts_generation_points() -> None:
    index = FakeVectorIndex()
    projector = VectorProjector(FakeSource(), FakeEmbedding(), index)  # type: ignore[arg-type]
    stats = await projector.rebuild(_REPO)

    assert stats.files == 2
    assert stats.points == stats.chunks >= 3
    assert stats.generation_id == "gen_" + "c" * 32
    assert index.ensured == 1
    assert index.deleted == ["gen_" + "c" * 32]
    assert all(point.commit_sha == _SHA for point in index.points)
    assert len({point.point_id for point in index.points}) == len(index.points)


class FakeProvider:
    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        return _descriptor()


class FakeRegistry:
    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return (_descriptor(),)

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor | None:
        return _descriptor()


class FakeToolIndex:
    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary:
        return IndexSnapshotSummary(
            repository_id=repository_id,
            commit_sha=_SHA,
            generation_id="gen_" + "c" * 32,
            generation_number=1,
            status=IndexGenerationStatus.ACTIVE,
        )


class FakeVectorQuery:
    async def search(
        self,
        repository_id: RepositoryId,
        generation_id: str,
        query: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        return (
            VectorSearchHit(
                repository_id=repository_id,
                commit_sha=_SHA,
                path=RepositoryPath(PurePosixPath("src/client.ts")),
                start_line=4,
                end_line=8,
                excerpt="export class Client {}",
                score=0.9,
            ),
        )


def _descriptor() -> RepositoryDescriptor:
    return RepositoryDescriptor(
        repository_id=_REPO,
        canonical_location=RepositoryLocation(Path(".")),
        name="linejs",
        default_branch="main",
        registered_at=_NOW,
    )


@pytest.mark.asyncio
async def test_search_vector_tool_returns_verified_evidence() -> None:
    services = ToolServices(
        repository=FakeProvider(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        index=FakeToolIndex(),  # type: ignore[arg-type]
        vector=FakeVectorQuery(),
    )
    result = await search_vector(
        services,
        ToolContext(request_id="req_vector_1"),
        SearchVectorInput(query="Client export", limit=5),
    )
    assert result.evidence[0].retrieval_method == "vector_search"
    assert result.evidence[0].path.as_posix() == "src/client.ts"
    payload = json.loads(json.dumps(result.payload))
    assert payload["count"] == 1
