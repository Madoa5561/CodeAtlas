"""Async Ollama chat adapter that normalizes responses for the model port."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

import httpx

from codeatlas.config.settings import OllamaSettings
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

PROVIDER_ID = "ollama"

_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_BACKOFF_SECONDS = 30.0
_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_CONNECTIONS = 4
_MAX_KEEPALIVE_CONNECTIONS = 2
_SAFE_CALL_ID = re.compile(r"^[\w:.\-]{1,200}$")


def _provider_failure(kind: str) -> ModelProviderError:
    """Build a provider error whose internal cause never carries payload data."""
    return ModelProviderError(internal_cause=RuntimeError(f"ollama provider failure: {kind}"))


def _response_failure(kind: str) -> ModelResponseError:
    """Build a response error whose internal cause never carries payload data."""
    return ModelResponseError(internal_cause=RuntimeError(f"ollama response failure: {kind}"))


def _token_count(value: object) -> int:
    """Return a non-negative usage counter, ignoring provider anomalies."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


class OllamaModelProvider:
    """Provider-neutral model port backed by the local Ollama chat API."""

    def __init__(
        self,
        settings: OllamaSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        if settings.base_url.username or settings.base_url.password:
            raise ValueError("ollama base URL must not include credentials")
        if settings.base_url.query or settings.base_url.fragment:
            raise ValueError("ollama base URL must not include a query or fragment")
        self._settings = settings
        base_url = str(settings.base_url).rstrip("/")
        if not base_url:
            raise ValueError("ollama base URL is invalid")
        self._chat_url = f"{base_url}{_CHAT_PATH}"
        self._tags_url = f"{base_url}{_TAGS_PATH}"
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @classmethod
    def from_settings(
        cls,
        settings: OllamaSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> OllamaModelProvider:
        """Build a provider from validated Ollama settings."""
        return cls(settings, transport=transport, client=client)

    @property
    def provider_id(self) -> str:
        """Return the stable provider identifier."""
        return PROVIDER_ID

    @property
    def model_id(self) -> str:
        """Return the configured model name or an empty string when unset."""
        return self._settings.model or ""

    @property
    def enabled(self) -> bool:
        """Return whether the provider is enabled by configuration."""
        return self._settings.enabled

    @property
    def closed(self) -> bool:
        """Return whether resources were already released."""
        return self._closed

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one bounded request and normalize the response."""
        self._ensure_usable()
        payload = self._build_payload(request)
        timeout = httpx.Timeout(min(request.timeout_seconds, self._settings.timeout_seconds))
        max_retries = self._settings.max_retries
        for attempt in range(max_retries + 1):
            try:
                response = await self._client.post(self._chat_url, json=payload, timeout=timeout)
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt < max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise _provider_failure("connect_or_timeout") from None
            except httpx.HTTPError:
                raise _provider_failure("transport") from None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise _provider_failure(f"retryable_status_{response.status_code}") from None
            if response.status_code >= 400:
                raise _provider_failure(f"status_{response.status_code}") from None
            return self._parse_response(response)
        raise _provider_failure("retry_exhausted")

    async def check_available(self) -> bool:
        """Probe the tags endpoint once without failing the caller."""
        if not self.enabled or self._closed or self._client.is_closed:
            return False
        timeout = httpx.Timeout(min(self._settings.timeout_seconds, _PROBE_TIMEOUT_SECONDS))
        try:
            response = await self._client.get(self._tags_url, timeout=timeout)
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

    def _create_client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self._settings.timeout_seconds),
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
            "keep_alive": self._settings.keep_alive,
            "think": self._settings.think,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
                "num_ctx": self._settings.num_ctx,
            },
        }
        if request.tools:
            payload["tools"] = self._build_tools(request.tools)
        if request.response_format is ModelResponseFormat.JSON:
            payload["format"] = "json"
        return payload

    def _build_messages(self, messages: tuple[ModelMessage, ...]) -> list[dict[str, Any]]:
        tool_names: dict[str, str] = {}
        built: list[dict[str, Any]] = []
        for message in messages:
            if message.role is ModelRole.TOOL:
                tool_message: dict[str, Any] = {"role": "tool", "content": message.content}
                tool_name = tool_names.get(message.tool_call_id or "")
                if tool_name is not None:
                    tool_message["tool_name"] = tool_name
                built.append(tool_message)
            elif message.role is ModelRole.ASSISTANT:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content,
                }
                if message.tool_calls:
                    assistant_message["tool_calls"] = [
                        {"function": {"name": call.tool_name, "arguments": call.arguments()}}
                        for call in message.tool_calls
                    ]
                    for call in message.tool_calls:
                        tool_names[call.call_id] = call.tool_name
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
        delay = min(self._settings.retry_backoff_seconds * float(2**attempt), _MAX_BACKOFF_SECONDS)
        if delay > 0.0:
            await asyncio.sleep(delay)

    def _parse_response(self, response: httpx.Response) -> ModelResponse:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise _response_failure("response_too_large")
        try:
            payload: Any = response.json()
        except ValueError:
            raise _response_failure("invalid_json") from None
        if not isinstance(payload, dict):
            raise _response_failure("invalid_payload")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise _response_failure("missing_message")
        # A thinking model may emit reasoning in ``message.thinking``; that text is
        # never answer content, so only ``message.content`` is normalized.
        content = self._content(message.get("content"))
        tool_calls = self._tool_calls(message.get("tool_calls"))
        finish_reason = payload.get("done_reason")
        if not isinstance(finish_reason, str) or not finish_reason.strip():
            finish_reason = "stop"
        usage = ModelUsage(
            prompt_tokens=_token_count(payload.get("prompt_eval_count")),
            completion_tokens=_token_count(payload.get("eval_count")),
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
            raise _response_failure("invalid_response") from None

    @staticmethod
    def _content(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        raise _response_failure("invalid_content")

    def _tool_calls(self, value: object) -> tuple[CanonicalToolCall, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise _response_failure("invalid_tool_calls")
        return tuple(self._tool_call(index, item) for index, item in enumerate(value))

    def _tool_call(self, index: int, value: object) -> CanonicalToolCall:
        if not isinstance(value, dict):
            raise _response_failure("invalid_tool_call")
        function = value.get("function")
        if not isinstance(function, dict):
            raise _response_failure("invalid_tool_call")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise _response_failure("invalid_tool_name")
        arguments_json = self._arguments_json(function.get("arguments"))
        call_id = self._call_id(value.get("id"), index, name, arguments_json)
        try:
            return CanonicalToolCall(
                call_id=call_id,
                tool_name=name,
                arguments_json=arguments_json,
            )
        except ValueError:
            raise _response_failure("invalid_tool_call") from None

    @staticmethod
    def _call_id(value: object, index: int, tool_name: str, arguments_json: str) -> str:
        if isinstance(value, str) and _SAFE_CALL_ID.fullmatch(value) is not None:
            return value
        digest = hashlib.sha256(f"{index}:{tool_name}:{arguments_json}".encode()).hexdigest()
        return f"call_{digest[:32]}"

    @staticmethod
    def _arguments_json(arguments: object) -> str:
        parsed: Any = arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                raise _response_failure("invalid_tool_arguments") from None
        if not isinstance(parsed, dict):
            raise _response_failure("invalid_tool_arguments")
        try:
            return json.dumps(
                parsed,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise _response_failure("invalid_tool_arguments") from None
