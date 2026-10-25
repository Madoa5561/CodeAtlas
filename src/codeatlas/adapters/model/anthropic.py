"""Anthropic Messages API provider adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from codeatlas.config.settings import AnthropicSettings
from codeatlas.domain.agent_models import (
    ModelRequest,
    ModelResponse,
    ModelResponseFormat,
    ModelRole,
    ModelUsage,
)
from codeatlas.domain.errors import ModelProviderError, ModelResponseError

PROVIDER_ID = "anthropic"
_MAX_RESPONSE_BYTES = 4_000_000
_SAFE_MAX_TOKENS = 8_192


def _failure(kind: str) -> ModelProviderError:
    return ModelProviderError(internal_cause=RuntimeError(f"anthropic provider failure: {kind}"))


class AnthropicModelProvider:
    """Provider-neutral model port backed by the Anthropic Messages API."""

    def __init__(
        self,
        settings: AnthropicSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._provider_id = PROVIDER_ID
        self._model = settings.model
        self._base_url = str(settings.base_url).rstrip("/")
        self._api_key = settings.api_key.get_secret_value() if settings.api_key else ""
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def check_available(self) -> bool:
        """Probe the API without consuming model tokens."""
        response = await self._request("GET", "/v1/models")
        return response.status_code == 200

    @classmethod
    def from_settings(
        cls,
        settings: AnthropicSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> AnthropicModelProvider:
        """Build a provider from validated settings."""
        return cls(settings, transport=transport, client=client)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one bounded text/JSON request."""
        system = "\n\n".join(
            message.content for message in request.messages if message.role is ModelRole.SYSTEM
        )
        messages = [
            {
                "role": "user" if message.role in {ModelRole.USER, ModelRole.TOOL} else "assistant",
                "content": message.content,
            }
            for message in request.messages
            if message.role not in {ModelRole.SYSTEM}
        ]
        if not messages:
            raise _failure("messages")
        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": min(request.max_output_tokens, _SAFE_MAX_TOKENS),
            "temperature": request.temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if request.response_format is ModelResponseFormat.JSON:
            body["system"] = (
                system + "\n\n" if system else ""
            ) + "Respond with a single valid JSON object and no surrounding prose."
        response = await self._request("POST", "/v1/messages", json_body=body)
        if response.status_code in {401, 403}:
            raise _failure("authentication")
        if response.status_code == 429:
            raise _failure("rate_limited")
        if response.status_code >= 400:
            raise _failure("status")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise _failure("json") from None
        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise _failure("content")
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise ModelResponseError(internal_cause=RuntimeError("anthropic empty response"))
        usage = payload.get("usage")
        prompt_tokens = 0
        completion_tokens = 0
        if isinstance(usage, dict):
            prompt_tokens = _token(usage.get("input_tokens"))
            completion_tokens = _token(usage.get("output_tokens"))
        stop_reason = payload.get("stop_reason")
        return ModelResponse(
            content=text,
            finish_reason=str(stop_reason) if isinstance(stop_reason, str) else "stop",
            usage=ModelUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            model_id=self._model,
        )

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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        if self._closed or self._client.is_closed:
            raise _failure("closed")
        retries = self._settings.max_retries
        for attempt in range(retries + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json_body,
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise _failure("connection") from None
            except httpx.HTTPError:
                raise _failure("http") from None
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise _failure("response_size")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
            return response
        raise _failure("retries")


def _token(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
