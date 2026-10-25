"""OpenAI-compatible embedding HTTP adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import httpx

from codeatlas.config.settings import EmbeddingSettings
from codeatlas.domain.errors import EmbeddingProviderError

_MAX_RESPONSE_BYTES = 8_000_000
_MAX_BACKOFF_SECONDS = 10.0


class OpenAICompatibleEmbedding:
    """Bounded embeddings client compatible with OpenAI-style APIs."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        if settings.api_key is None or not settings.api_key.get_secret_value().strip():
            raise ValueError("embedding.api_key is required for openai-compatible")
        self._settings = settings
        self._dimensions = settings.dimensions
        self._model_id = settings.model
        self._base_url = str(settings.api_url).rstrip("/")
        self._api_key = settings.api_key.get_secret_value()
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Embed texts in bounded batches, preserving input order."""
        if not texts:
            return ()
        results: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._settings.batch_size):
            batch = list(texts[start : start + self._settings.batch_size])
            payload = await self._post({"model": self._model_id, "input": batch})
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingProviderError()
            ordered: list[tuple[float, ...] | None] = [None] * len(batch)
            for item in data:
                if not isinstance(item, dict):
                    raise EmbeddingProviderError()
                index = item.get("index")
                vector = item.get("embedding")
                if (
                    not isinstance(index, int)
                    or not 0 <= index < len(batch)
                    or not isinstance(vector, list)
                ):
                    raise EmbeddingProviderError()
                converted = tuple(float(value) for value in vector)
                if len(converted) != self._dimensions:
                    raise EmbeddingProviderError()
                ordered[index] = converted
            if any(vector is None for vector in ordered):
                raise EmbeddingProviderError()
            results.extend(vector for vector in ordered if vector is not None)
        return tuple(results)

    async def aclose(self) -> None:
        """Release owned resources."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def _create_client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self._settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def _post(self, body: dict[str, object]) -> dict[str, object]:
        if self._closed or self._client.is_closed:
            raise EmbeddingProviderError()
        for attempt in range(2):
            try:
                response = await self._client.post(
                    f"{self._base_url}/embeddings",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise EmbeddingProviderError() from None
            except httpx.HTTPError:
                raise EmbeddingProviderError() from None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise EmbeddingProviderError() from None
            if response.status_code >= 400 or len(response.content) > _MAX_RESPONSE_BYTES:
                raise EmbeddingProviderError()
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                raise EmbeddingProviderError() from None
            if not isinstance(payload, dict):
                raise EmbeddingProviderError()
            return payload
        raise EmbeddingProviderError()
