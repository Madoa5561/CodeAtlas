"""Async OpenAI-compatible chat adapter with canonical normalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

import httpx

from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseFormat,
    ModelRole,
    ModelUsage,
    ToolDefinition,
)
from codeatlas.domain.errors import ModelProviderError, ModelResponseError

_MAX_RESPONSE_BYTES = 4_000_000
_MAX_BACKOFF_SECONDS = 30.0
_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_CONNECTIONS = 4
_MAX_KEEPALIVE_CONNECTIONS = 2
_SAFE_CALL_ID = re.compile(r"^[\w:.\-]{1,200}$")
_SAFE_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _provider_failure(provider_id: str, kind: str) -> ModelProviderError:
    """Build a provider error whose internal cause never carries payload data."""
    return ModelProviderError(
        internal_cause=RuntimeError(f"{provider_id} provider failure: {kind}")
    )


def _response_failure(provider_id: str, kind: str) -> ModelResponseError:
    """Build a response error whose internal cause never carries payload data."""
    return ModelResponseError(
        internal_cause=RuntimeError(f"{provider_id} response failure: {kind}")
    )


def _token_count(value: object) -> int:
    """Return a non-negative usage counter, ignoring provider anomalies."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


class OpenAICompatibleModelProvider:
    """Provider-neutral model port backed by an OpenAI-compatible chat API.

    DeepSeek, OpenAI-compatible gateways, and compatible local servers can use
    this adapter. Provider-specific payload switches are limited to explicit
    constructor parameters so no provider name leaks into the domain layer.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_key: str,
        enabled: bool,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        thinking: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        if _SAFE_PROVIDER_ID.fullmatch(provider_id) is None:
            raise ValueError("invalid provider identifier")
        if not model.strip():
            raise ValueError("model identifier is required")
        if enabled and not api_key.strip():
            raise ValueError("an API key is required when the provider is enabled")
        parsed = httpx.URL(base_url)
        if parsed.username or parsed.password:
            raise ValueError("provider base URL must not include credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("provider base URL must not include a query or fragment")
        if not base_url.rstrip("/"):
            raise ValueError("provider base URL is invalid")
        self._provider_id = provider_id
        self._model = model
        self._api_key = api_key
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._thinking = thinking
        base = base_url.rstrip("/")
        self._chat_url = f"{base}/chat/completions"
        self._models_url = f"{base}/models"
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @property
    def provider_id(self) -> str:
        """Return the stable provider identifier."""
        return self._provider_id

    @property
    def model_id(self) -> str:
        """Return the configured model identifier."""
        return self._model

    @property
    def enabled(self) -> bool:
        """Return whether the provider is enabled by configuration."""
        return self._enabled

    @property
    def closed(self) -> bool:
        """Return whether resources were already released."""
        return self._closed

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one bounded request and normalize the response."""
        self._ensure_usable()
        payload = self._build_payload(request)
        timeout = httpx.Timeout(min(request.timeout_seconds, self._timeout_seconds))
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    self._chat_url,
                    json=payload,
                    headers=self._headers(),
                    timeout=timeout,
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt < self._max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise _provider_failure(self._provider_id, "connect_or_timeout") from None
            except httpx.HTTPError:
                raise _provider_failure(self._provider_id, "transport") from None
            if response.status_code in {401, 403}:
                raise _provider_failure(self._provider_id, "authentication") from None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < self._max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise _provider_failure(
                    self._provider_id, f"retryable_status_{response.status_code}"
                ) from None
            if response.status_code >= 400:
                raise _provider_failure(
                    self._provider_id, f"status_{response.status_code}"
                ) from None
            return self._parse_response(response)
        raise _provider_failure(self._provider_id, "retry_exhausted")

    async def check_available(self) -> bool:
        """Probe the models endpoint once without failing the caller."""
        if not self.enabled or self._closed or self._client.is_closed:
            return False
        timeout = httpx.Timeout(min(self._timeout_seconds, _PROBE_TIMEOUT_SECONDS))
        try:
            response = await self._client.get(
                self._models_url,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    async def aclose(self) -> None:
        """Release owned resources; repeated calls are safe."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _create_client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=_MAX_CONNECTIONS,
                max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
            ),
        )

    def _ensure_usable(self) -> None:
        if not self.enabled:
            raise ModelProviderError()
        if self._closed or self._client.is_closed:
            raise ModelProviderError()

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": self._build_messages(request.messages),
            "stream": False,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = self._build_tools(request.tools)
            payload["tool_choice"] = "auto"
        if request.response_format is ModelResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}
        if self._thinking is not None:
            payload["thinking"] = {"type": "enabled" if self._thinking else "disabled"}
        return payload

    def _build_messages(self, messages: tuple[ModelMessage, ...]) -> list[dict[str, Any]]:
        built: list[dict[str, Any]] = []
        for message in messages:
            if message.role is ModelRole.TOOL:
                built.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": message.tool_call_id,
                    }
                )
            elif message.role is ModelRole.ASSISTANT:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content,
                }
                if message.tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.tool_name,
                                "arguments": call.arguments_json,
                            },
                        }
                        for call in message.tool_calls
                    ]
                built.append(assistant_message)
            else:
                built.append({"role": message.role.value, "content": message.content})
        return built

    @staticmethod
    def _build_tools(tools: tuple[ToolDefinition, ...]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": json.loads(tool.input_schema_json),
                },
            }
            for tool in tools
        ]

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = min(self._retry_backoff_seconds * float(2**attempt), _MAX_BACKOFF_SECONDS)
        if delay > 0.0:
            await asyncio.sleep(delay)

    def _parse_response(self, response: httpx.Response) -> ModelResponse:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise _response_failure(self._provider_id, "response_too_large")
        try:
            payload: Any = response.json()
        except ValueError:
            raise _response_failure(self._provider_id, "invalid_json") from None
        if not isinstance(payload, dict):
            raise _response_failure(self._provider_id, "invalid_payload")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _response_failure(self._provider_id, "missing_choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise _response_failure(self._provider_id, "invalid_choice")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise _response_failure(self._provider_id, "missing_message")
        # ``reasoning_content`` belongs to thinking mode and is never answer text.
        content = self._content(message.get("content"))
        tool_calls = self._tool_calls(message.get("tool_calls"))
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason.strip():
            finish_reason = "stop"
        usage_payload = payload.get("usage")
        usage = (
            ModelUsage(
                prompt_tokens=_token_count(usage_payload.get("prompt_tokens")),
                completion_tokens=_token_count(usage_payload.get("completion_tokens")),
            )
            if isinstance(usage_payload, dict)
            else ModelUsage()
        )
        reported_model = payload.get("model")
        model_id = (
            reported_model
            if isinstance(reported_model, str) and reported_model.strip()
            else self.model_id
        )
        try:
            return ModelResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                model_id=model_id,
            )
        except ValueError:
            raise _response_failure(self._provider_id, "invalid_response") from None

    def _content(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        raise _response_failure(self._provider_id, "invalid_content")

    def _tool_calls(self, value: object) -> tuple[CanonicalToolCall, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise _response_failure(self._provider_id, "invalid_tool_calls")
        return tuple(self._tool_call(index, item) for index, item in enumerate(value))

    def _tool_call(self, index: int, value: object) -> CanonicalToolCall:
        if not isinstance(value, dict):
            raise _response_failure(self._provider_id, "invalid_tool_call")
        function = value.get("function")
        if not isinstance(function, dict):
            raise _response_failure(self._provider_id, "invalid_tool_call")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise _response_failure(self._provider_id, "invalid_tool_name")
        arguments_json = self._arguments_json(function.get("arguments"))
        call_id = self._call_id(value.get("id"), index, name, arguments_json)
        try:
            return CanonicalToolCall(
                call_id=call_id,
                tool_name=name,
                arguments_json=arguments_json,
            )
        except ValueError:
            raise _response_failure(self._provider_id, "invalid_tool_call") from None

    @staticmethod
    def _call_id(value: object, index: int, tool_name: str, arguments_json: str) -> str:
        if isinstance(value, str) and _SAFE_CALL_ID.fullmatch(value) is not None:
            return value
        digest = hashlib.sha256(f"{index}:{tool_name}:{arguments_json}".encode()).hexdigest()
        return f"call_{digest[:32]}"

    def _arguments_json(self, arguments: object) -> str:
        parsed: Any = arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                raise _response_failure(self._provider_id, "invalid_tool_arguments") from None
        if not isinstance(parsed, dict):
            raise _response_failure(self._provider_id, "invalid_tool_arguments")
        try:
            return json.dumps(
                parsed,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise _response_failure(self._provider_id, "invalid_tool_arguments") from None
