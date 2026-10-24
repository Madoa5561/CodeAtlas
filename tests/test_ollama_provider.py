"""Tests for the Ollama model provider adapter and its plugin."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import HttpUrl

from codeatlas.adapters.model import (
    OLLAMA_CAPABILITY,
    OllamaModelPlugin,
    OllamaModelProvider,
)
from codeatlas.config.settings import OllamaSettings
from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    ModelMessage,
    ModelRequest,
    ModelResponseFormat,
    ModelRole,
    ToolDefinition,
)
from codeatlas.domain.errors import ModelProviderError, ModelResponseError
from codeatlas.plugin_api import PluginOrigin, PluginRegistry

Handler = Callable[[httpx.Request], httpx.Response]

_LIVE_ENABLED = os.environ.get("CODEATLAS_TEST_OLLAMA") == "1"
_GENERATED_CALL_ID = re.compile(r"^call_[0-9a-f]{32}$")


def make_settings(
    *,
    enabled: bool = True,
    model: str | None = "test-model",
    timeout_seconds: float = 5.0,
    max_retries: int = 0,
    retry_backoff_seconds: float = 0.0,
    keep_alive: str = "5m",
    think: bool = False,
    num_ctx: int = 8_192,
) -> OllamaSettings:
    """Build valid Ollama settings with deterministic test defaults."""
    return OllamaSettings(
        enabled=enabled,
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        keep_alive=keep_alive,
        think=think,
        num_ctx=num_ctx,
    )


def provider_for(handler: Handler, settings: OllamaSettings | None = None) -> OllamaModelProvider:
    """Build a provider whose HTTP traffic is fully mocked."""
    resolved = settings if settings is not None else make_settings()
    return OllamaModelProvider(resolved, transport=httpx.MockTransport(handler))


def request_for(
    *messages: ModelMessage,
    tools: tuple[ToolDefinition, ...] = (),
    response_format: ModelResponseFormat = ModelResponseFormat.TEXT,
    temperature: float = 0.2,
    max_output_tokens: int = 1_200,
    timeout_seconds: float = 10.0,
) -> ModelRequest:
    """Build a canonical model request for adapter tests."""
    return ModelRequest(
        messages=messages,
        tools=tools,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        response_format=response_format,
    )


def chat_payload(**overrides: Any) -> dict[str, Any]:
    """Return a representative Ollama chat response body."""
    payload: dict[str, Any] = {
        "model": "test-model",
        "message": {"role": "assistant", "content": "Hello"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 5,
    }
    payload.update(overrides)
    return payload


def request_body(request: httpx.Request) -> dict[str, Any]:
    """Decode the JSON body captured by a mock transport."""
    decoded: Any = json.loads(request.content)
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.asyncio
async def test_text_completion_is_normalized() -> None:
    """A plain chat response maps onto the canonical model response."""
    requests: list[httpx.Request] = []
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(request_body(request))
        return httpx.Response(200, json=chat_payload())

    provider = provider_for(handler)
    response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert provider.provider_id == "ollama"
    assert provider.model_id == "test-model"
    assert provider.enabled is True
    assert response.content == "Hello"
    assert response.tool_calls == ()
    assert response.finish_reason == "stop"
    assert response.usage.prompt_tokens == 12
    assert response.usage.completion_tokens == 5
    assert response.model_id == "test-model"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/chat"
    assert bodies[0]["model"] == "test-model"
    assert bodies[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert bodies[0]["stream"] is False
    assert bodies[0]["keep_alive"] == "5m"
    assert bodies[0]["think"] is False
    assert bodies[0]["options"] == {"temperature": 0.2, "num_predict": 1_200, "num_ctx": 8_192}
    assert "tools" not in bodies[0]
    assert "format" not in bodies[0]


@pytest.mark.asyncio
async def test_think_setting_is_forwarded() -> None:
    """The thinking flag is sent top-level for thinking-capable models."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request_body(request))
        return httpx.Response(200, json=chat_payload())

    provider = provider_for(handler, make_settings(think=True))
    await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert bodies[0]["think"] is True


@pytest.mark.asyncio
async def test_tool_calls_are_normalized() -> None:
    """Ollama tool calls become validated canonical tool calls."""
    requests: list[httpx.Request] = []
    body = chat_payload(
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_provider_1",
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "src/main.py", "start_line": 1},
                    },
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    schema = '{"type": "object", "properties": {"path": {"type": "string"}}}'
    tool = ToolDefinition(name="read_file", description="Read a file", input_schema_json=schema)
    response = await provider.complete(
        request_for(ModelMessage(ModelRole.USER, "read it"), tools=(tool,))
    )
    await provider.aclose()

    assert response.content == ""
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.call_id == "call_provider_1"
    assert call.tool_name == "read_file"
    assert call.arguments() == {"path": "src/main.py", "start_line": 1}
    payload = request_body(requests[0])
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_multiple_tool_calls_and_deterministic_ids() -> None:
    """Calls stay ordered and generated IDs are safe and deterministic."""
    body = chat_payload(
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "find_symbol", "arguments": {"name": "main"}}},
                {"function": {"name": "search_text", "arguments": '{"query": "needle"}'}},
            ],
        }
    )
    seen: list[tuple[str, ...]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    for _ in range(2):
        response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "go")))
        seen.append(tuple(call.call_id for call in response.tool_calls))
        assert [call.tool_name for call in response.tool_calls] == ["find_symbol", "search_text"]
        assert response.tool_calls[0].arguments() == {"name": "main"}
        assert response.tool_calls[1].arguments() == {"query": "needle"}
        assert all(_GENERATED_CALL_ID.fullmatch(call_id) for call_id in seen[-1])
    await provider.aclose()

    assert seen[0] == seen[1]
    assert len(set(seen[0])) == 2


@pytest.mark.asyncio
async def test_unsafe_provider_call_id_is_replaced() -> None:
    """A provider ID that violates the safe-ID rule gets a generated identity."""
    body = chat_payload(
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "id with spaces/and?query",
                    "function": {"name": "read_file", "arguments": {"path": "a.py"}},
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "go")))
    await provider.aclose()

    assert _GENERATED_CALL_ID.fullmatch(response.tool_calls[0].call_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["{not json", '["path"]', None, "42", ["path"]])
async def test_malformed_tool_arguments_are_rejected_safely(arguments: object) -> None:
    """Malformed arguments raise a response error that leaks no payload."""
    prompt = "SUPER-SECRET-PROMPT"
    body = chat_payload(
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read_file", "arguments": arguments}}],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    with pytest.raises(ModelResponseError) as captured:
        await provider.complete(request_for(ModelMessage(ModelRole.USER, prompt)))
    await provider.aclose()

    assert prompt not in str(captured.value)
    assert prompt not in repr(captured.value)
    assert "not json" not in str(captured.value)
    assert type(captured.value) is ModelResponseError


@pytest.mark.asyncio
async def test_server_error_retries_then_succeeds() -> None:
    """Transient 5xx responses are retried up to the configured bound."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(500, json={"error": "transient"})
        return httpx.Response(200, json=chat_payload())

    provider = provider_for(handler, make_settings(max_retries=2))
    response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert response.content == "Hello"
    assert attempts == 3


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds() -> None:
    """HTTP 429 is treated as a retryable provider failure."""
    statuses = [429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return httpx.Response(status, json=chat_payload())

    provider = provider_for(handler, make_settings(max_retries=1))
    response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert response.content == "Hello"
    assert statuses == []


@pytest.mark.asyncio
async def test_exhausted_retries_raise_safe_provider_error() -> None:
    """Retry exhaustion maps to a provider error without payload leakage."""
    provider_detail = "provider-internal-trace"
    prompt = "PROMPT-SECRET"
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": provider_detail})

    provider = provider_for(handler, make_settings(max_retries=1))
    with pytest.raises(ModelProviderError) as captured:
        await provider.complete(request_for(ModelMessage(ModelRole.USER, prompt)))
    await provider.aclose()

    assert attempts == 2
    assert type(captured.value) is ModelProviderError
    assert prompt not in str(captured.value)
    assert prompt not in repr(captured.value)
    assert provider_detail not in str(captured.value)
    assert provider_detail not in repr(captured.value)
    assert captured.value.internal_cause is not None
    assert provider_detail not in repr(captured.value.internal_cause)


@pytest.mark.asyncio
async def test_timeout_retries_and_maps_to_provider_error() -> None:
    """Connect/timeout failures are retried and then mapped safely."""
    timeout_detail = "timed out talking to http://localhost:11434"
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout(timeout_detail)

    provider = provider_for(handler, make_settings(max_retries=2))
    with pytest.raises(ModelProviderError) as captured:
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert attempts == 3
    assert timeout_detail not in str(captured.value)
    assert timeout_detail not in repr(captured.value)
    assert timeout_detail not in repr(captured.value.internal_cause)


@pytest.mark.asyncio
async def test_non_retryable_status_is_not_retried() -> None:
    """A client-side HTTP error is reported immediately."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": "bad request"})

    provider = provider_for(handler, make_settings(max_retries=3))
    with pytest.raises(ModelProviderError):
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert attempts == 1


@pytest.mark.asyncio
async def test_malformed_response_json_is_response_error() -> None:
    """An unparsable body maps to a response error without echoing the body."""
    body_secret = "not-json-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body_secret.encode())

    provider = provider_for(handler)
    with pytest.raises(ModelResponseError) as captured:
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert body_secret not in str(captured.value)
    assert body_secret not in repr(captured.value)


@pytest.mark.asyncio
async def test_empty_completion_is_response_error() -> None:
    """A response with neither content nor tool calls is unusable."""
    body = chat_payload(message={"role": "assistant", "content": ""})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    with pytest.raises(ModelResponseError):
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()


@pytest.mark.asyncio
async def test_thinking_text_is_not_answer_content() -> None:
    """Reasoning streamed into message.thinking is never treated as content."""
    body = chat_payload(
        message={"role": "assistant", "content": "", "thinking": "internal reasoning"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    with pytest.raises(ModelResponseError) as captured:
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert "internal reasoning" not in repr(captured.value)
    assert "internal reasoning" not in repr(captured.value.internal_cause)


@pytest.mark.asyncio
async def test_json_response_mode_requests_json_format() -> None:
    """JSON response mode asks Ollama for strict JSON output."""
    bodies: list[dict[str, Any]] = []
    content = '{"answer": "OK"}'

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request_body(request))
        return httpx.Response(
            200, json=chat_payload(message={"role": "assistant", "content": content})
        )

    provider = provider_for(handler)
    response = await provider.complete(
        request_for(
            ModelMessage(ModelRole.USER, "answer as json"),
            response_format=ModelResponseFormat.JSON,
        )
    )
    await provider.aclose()

    assert bodies[0]["format"] == "json"
    assert response.content == content


@pytest.mark.asyncio
async def test_conversation_messages_are_converted() -> None:
    """Canonical roles, tool calls, and tool results use Ollama's wire format."""
    bodies: list[dict[str, Any]] = []
    call = CanonicalToolCall(
        call_id="call-1",
        tool_name="read_file",
        arguments_json='{"path": "a.py"}',
    )
    messages = (
        ModelMessage(ModelRole.SYSTEM, "be brief"),
        ModelMessage(ModelRole.USER, "read a.py"),
        ModelMessage(ModelRole.ASSISTANT, "", tool_calls=(call,)),
        ModelMessage(ModelRole.TOOL, "file body", tool_call_id="call-1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request_body(request))
        return httpx.Response(200, json=chat_payload())

    provider = provider_for(handler)
    await provider.complete(request_for(*messages))
    await provider.aclose()

    assert bodies[0]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "read a.py"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}],
        },
        {"role": "tool", "content": "file body", "tool_name": "read_file"},
    ]


@pytest.mark.asyncio
async def test_disabled_provider_never_calls_network() -> None:
    """A disabled provider rejects completion without touching the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the network must not be used")

    provider = provider_for(handler, make_settings(enabled=False, model=None))
    assert provider.enabled is False
    assert provider.model_id == ""
    with pytest.raises(ModelProviderError):
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    assert await provider.check_available() is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_blocks_completion() -> None:
    """Closing twice is safe and the provider refuses further work."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_payload())

    provider = provider_for(handler)
    await provider.aclose()
    await provider.aclose()

    with pytest.raises(ModelProviderError):
        await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))


@pytest.mark.parametrize(
    ("base_url", "secret"),
    [
        ("http://localhost:11434/?token=super-secret", "super-secret"),
        ("http://user:super-secret@localhost:11434", "super-secret"),
        ("http://localhost:11434/#super-secret", "super-secret"),
    ],
)
def test_unsafe_base_url_is_rejected_without_leaking(base_url: str, secret: str) -> None:
    """Credentialed or query-bearing base URLs are rejected without echoing them."""
    settings = OllamaSettings(
        enabled=True,
        model="test-model",
        base_url=HttpUrl(base_url),
    )
    with pytest.raises(ValueError) as captured:
        OllamaModelProvider(settings)
    assert secret not in str(captured.value)


@pytest.mark.asyncio
async def test_missing_done_reason_and_model_fall_back_to_defaults() -> None:
    """Absent optional metadata falls back to canonical defaults."""
    body = {"message": {"role": "assistant", "content": "hi"}, "done": True}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = provider_for(handler)
    response = await provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))
    await provider.aclose()

    assert response.finish_reason == "stop"
    assert response.model_id == "test-model"
    assert response.usage.prompt_tokens == 0
    assert response.usage.completion_tokens == 0


def make_plugin_handler(
    requests: list[httpx.Request],
    *,
    tags_status: int = 200,
) -> Handler:
    """Build a transport handler that serves the tags probe and chat calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(tags_status, json={"models": []})
        return httpx.Response(200, json=chat_payload())

    return handler


def plugin_flags(plugin: OllamaModelPlugin) -> tuple[bool, bool]:
    """Read lifecycle flags without triggering mypy attribute narrowing."""
    return (plugin.started, plugin.available)


@pytest.mark.asyncio
async def test_plugin_lifecycle_reports_available() -> None:
    """The built-in plugin exposes model:ollama and probes reachability on start."""
    requests: list[httpx.Request] = []
    plugin = OllamaModelPlugin.from_settings(
        make_settings(),
        transport=httpx.MockTransport(make_plugin_handler(requests)),
    )

    assert plugin.manifest.origin is PluginOrigin.BUILT_IN
    assert plugin.manifest.capabilities[0].capability_id == OLLAMA_CAPABILITY
    assert OLLAMA_CAPABILITY == "model:ollama"
    assert plugin.model_id == "test-model"

    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)
    assert registry.resolve(OLLAMA_CAPABILITY) is plugin

    await registry.start_all()
    started_after_start, available_after_start = plugin_flags(plugin)
    assert started_after_start is True
    assert available_after_start is True
    assert plugin.degraded_reason is None
    assert [request.url.path for request in requests] == ["/api/tags"]

    await registry.stop_all()
    started_after_stop, available_after_stop = plugin_flags(plugin)
    assert started_after_stop is False
    assert available_after_stop is False
    with pytest.raises(ModelProviderError):
        await plugin.provider.complete(request_for(ModelMessage(ModelRole.USER, "hello")))


@pytest.mark.asyncio
async def test_plugin_start_survives_unreachable_ollama(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreachable endpoint degrades the optional capability without failing."""
    caplog.set_level(logging.INFO)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("http://localhost:11434 secret detail")

    plugin = OllamaModelPlugin.from_settings(
        make_settings(timeout_seconds=1.0),
        transport=httpx.MockTransport(handler),
    )
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)

    await registry.start_all()
    assert plugin.started is True
    assert plugin.available is False
    assert plugin.degraded_reason == "ollama_unreachable"
    assert "http://" not in caplog.text
    assert "secret detail" not in caplog.text

    await registry.stop_all()
    assert plugin.started is False


@pytest.mark.asyncio
async def test_plugin_disabled_skips_probe() -> None:
    """A disabled provider records degraded mode without network traffic."""
    requests: list[httpx.Request] = []
    plugin = OllamaModelPlugin.from_settings(
        make_settings(enabled=False, model=None),
        transport=httpx.MockTransport(make_plugin_handler(requests)),
    )
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)

    await registry.start_all()
    assert plugin.started is True
    assert plugin.available is False
    assert plugin.degraded_reason == "provider_disabled"
    assert requests == []

    await registry.stop_all()


@pytest.mark.asyncio
async def test_plugin_start_and_stop_are_idempotent() -> None:
    """Repeated lifecycle calls do not duplicate probes or raise."""
    requests: list[httpx.Request] = []
    plugin = OllamaModelPlugin.from_settings(
        make_settings(),
        transport=httpx.MockTransport(make_plugin_handler(requests)),
    )

    await plugin.start()
    await plugin.start()
    await plugin.stop()
    await plugin.stop()

    assert [request.url.path for request in requests] == ["/api/tags"]
    assert plugin.started is False
    assert plugin.available is False


@pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="set CODEATLAS_TEST_OLLAMA=1 to run against the local Ollama server",
)
@pytest.mark.asyncio
async def test_live_ollama_returns_canonical_text_response() -> None:
    """Opt-in smoke test against the real local Ollama model."""
    model = os.environ.get("CODEATLAS_OLLAMA_MODEL", "ornith-1.5-9b:q4_k_m")
    base_url = HttpUrl(os.environ.get("CODEATLAS_OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    settings = OllamaSettings(
        enabled=True,
        model=model,
        base_url=base_url,
        timeout_seconds=120.0,
        max_retries=0,
        think=False,
    )
    provider = OllamaModelProvider(settings)
    try:
        assert await provider.check_available() is True
        response = await provider.complete(
            ModelRequest(
                messages=(ModelMessage(ModelRole.USER, "Reply with the single word: pong"),),
                max_output_tokens=64,
                timeout_seconds=120.0,
            )
        )
    finally:
        await provider.aclose()

    assert response.content.strip()
    assert "pong" in response.content.casefold()
    assert response.model_id
