"""Qdrant REST adapter for generation-scoped vector projections."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

import httpx

from codeatlas.config.settings import QdrantSettings
from codeatlas.domain.errors import VectorIndexError
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.domain.vector_models import VectorPoint, VectorSearchHit

_MAX_RESPONSE_BYTES = 4_000_000
_MAX_BATCH = 64


class QdrantVectorIndex:
    """Bounded Qdrant client using the HTTP REST API."""

    def __init__(
        self,
        settings: QdrantSettings,
        *,
        dimensions: int,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if dimensions < 1:
            raise ValueError("vector dimensions must be positive")
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        self._settings = settings
        self._dimensions = dimensions
        self._base_url = str(settings.url).rstrip("/")
        self._api_key = (
            settings.api_key.get_secret_value() if settings.api_key is not None else None
        )
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def ensure_collection(self) -> None:
        """Create the collection if it does not exist."""
        response = await self._request(
            "PUT",
            f"/collections/{self._settings.collection}",
            json_body={
                "vectors": {"size": self._dimensions, "distance": "Cosine"},
            },
        )
        if response.status_code not in {200, 201, 409}:
            raise VectorIndexError()

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        """Upsert points in bounded batches."""
        for start in range(0, len(points), _MAX_BATCH):
            batch = points[start : start + _MAX_BATCH]
            payload = [
                {
                    "id": point.point_id,
                    "vector": list(point.vector),
                    "payload": {
                        "repository_id": point.repository_id.value,
                        "generation_id": point.generation_id,
                        "commit_sha": point.commit_sha,
                        "path": point.path.as_posix(),
                        "start_line": point.start_line,
                        "end_line": point.end_line,
                        "excerpt": point.excerpt,
                    },
                }
                for point in batch
            ]
            response = await self._request(
                "PUT",
                f"/collections/{self._settings.collection}/points",
                params={"wait": "true"},
                json_body={"points": payload},
            )
            if response.status_code not in {200, 201}:
                raise VectorIndexError()

    async def search(
        self,
        vector: Sequence[float],
        *,
        repository_id: RepositoryId,
        generation_id: str,
        limit: int,
    ) -> tuple[VectorSearchHit, ...]:
        """Search with repository and generation filters enforced server-side."""
        response = await self._request(
            "POST",
            f"/collections/{self._settings.collection}/points/search",
            json_body={
                "vector": list(vector),
                "limit": max(1, min(limit, 100)),
                "with_payload": True,
                "filter": self._filter(repository_id, generation_id),
            },
        )
        if response.status_code != 200:
            raise VectorIndexError()
        payload = self._json(response)
        result = payload.get("result")
        if not isinstance(result, list):
            raise VectorIndexError()
        hits: list[VectorSearchHit] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            score = item.get("score")
            point = item.get("payload")
            if not isinstance(score, int | float) or not isinstance(point, dict):
                continue
            try:
                hits.append(
                    VectorSearchHit(
                        repository_id=RepositoryId(str(point["repository_id"])),
                        commit_sha=str(point["commit_sha"]),
                        path=RepositoryPath(PurePosixPath(str(point["path"]))),
                        start_line=int(point["start_line"]),
                        end_line=int(point["end_line"]),
                        excerpt=str(point["excerpt"]),
                        score=float(score),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return tuple(hits)

    async def count(
        self,
        *,
        repository_id: RepositoryId,
        generation_id: str,
    ) -> int:
        """Count points for one repository generation."""
        response = await self._request(
            "POST",
            f"/collections/{self._settings.collection}/points/count",
            json_body={"filter": self._filter(repository_id, generation_id), "exact": True},
        )
        if response.status_code != 200:
            raise VectorIndexError()
        payload = self._json(response)
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("count"), int):
            raise VectorIndexError()
        return int(result["count"])

    async def delete_generation(self, generation_id: str) -> None:
        """Delete every point owned by one generation."""
        response = await self._request(
            "POST",
            f"/collections/{self._settings.collection}/points/delete",
            params={"wait": "true"},
            json_body={
                "filter": {
                    "must": [{"key": "generation_id", "match": {"value": generation_id}}],
                }
            },
        )
        if response.status_code not in {200, 201}:
            raise VectorIndexError()

    async def aclose(self) -> None:
        """Release owned resources."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _filter(repository_id: RepositoryId, generation_id: str) -> dict[str, object]:
        return {
            "must": [
                {"key": "repository_id", "match": {"value": repository_id.value}},
                {"key": "generation_id", "match": {"value": generation_id}},
            ]
        }

    def _create_client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self._settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        if self._closed or self._client.is_closed:
            raise VectorIndexError()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["api-key"] = self._api_key
        for attempt in range(2):
            try:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise VectorIndexError() from None
            except httpx.HTTPError:
                raise VectorIndexError() from None
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise VectorIndexError()
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise VectorIndexError() from None
            return response
        raise VectorIndexError()

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise VectorIndexError() from None
        if not isinstance(payload, dict):
            raise VectorIndexError()
        return payload
