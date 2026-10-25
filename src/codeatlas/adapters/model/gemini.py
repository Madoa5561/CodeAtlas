"""Google Gemini generateContent provider adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from codeatlas.config.settings import GeminiSettings
from codeatlas.domain.agent_models import (
    ModelRequest,
    ModelResponse,
    ModelResponseFormat,
    ModelRole,
    ModelUsage,
)
from codeatlas.domain.errors import ModelProviderError, ModelResponseError

PROVIDER_ID = "gemini"
_MAX_RESPONSE_BYTES = 4_000_000
_SAFE_MAX_TOKENS = 8_192


def _failure(kind: str) -> ModelProviderError:
    return ModelProviderError(internal_cause=RuntimeError(f"gemini provider failure: {kind}"))


class GeminiModelProvider:
    """Provider-neutral model port backed by the Gemini generateContent API."""

    def __init__(
        self,
        settings: GeminiSettings,
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
        """Probe model metadata without generating tokens."""
        response = await self._request("GET", f"/v1beta/models/{self._model}")
        return response.status_code == 200

    @classmethod
    def from_settings(
        cls,
        settings: GeminiSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> GeminiModelProvider:
        """Build a provider from validated settings."""
        return cls(settings, transport=transport, client=client)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one bounded text/JSON request."""
        system = "\n\n".join(
            message.content for message in request.messages if message.role is ModelRole.SYSTEM
        )
        contents: list[dict[str, object]] = []
        for message in request.messages:
            if message.role is ModelRole.SYSTEM:
                continue
            role = "model" if message.role is ModelRole.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})
        if not contents:
            raise _failure("messages")
        generation_config: dict[str, object] = {
            "temperature": request.temperature,
            "maxOutputTokens": min(request.max_output_tokens, _SAFE_MAX_TOKENS),
        }
        if request.response_format is ModelResponseFormat.JSON:
            generation_config["responseMimeType"] = "application/json"
        body: dict[str, object] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        response = await self._request(
            "POST",
            f"/v1beta/models/{self._model}:generateContent",
            json_body=body,
            with_key=True,
        )
        if response.status_code in {401, 403, 400}:
            raise _failure("request")
        if response.status_code == 429:
            raise _failure("rate_limited")
        if response.status_code >= 400:
            raise _failure("status")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            raise _failure("json") from None
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise _failure("candidates")
        first = candidates[0]
        if not isinstance(first, dict):
            raise _failure("candidate")
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise _failure("parts")
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise ModelResponseError(internal_cause=RuntimeError("gemini empty response"))
        usage = payload.get("usageMetadata")
        prompt_tokens = 0
        completion_tokens = 0
        if isinstance(usage, dict):
            prompt_tokens = _token(usage.get("promptTokenCount"))
            completion_tokens = _token(usage.get("candidatesTokenCount"))
        finish = first.get("finishReason")
        return ModelResponse(
            content=text,
            finish_reason=str(finish) if isinstance(finish, str) else "stop",
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
        with_key: bool = False,
    ) -> httpx.Response:
        if self._closed or self._client.is_closed:
            raise _failure("closed")
        params = {"key": self._api_key} if with_key else None
        retries = self._settings.max_retries
        for attempt in range(retries + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json_body,
                    headers={"Content-Type": "application/json"},
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
