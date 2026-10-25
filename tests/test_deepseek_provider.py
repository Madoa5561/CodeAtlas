"""Contract tests for the DeepSeek OpenAI-compatible model provider."""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import SecretStr

from codeatlas.adapters.model import DeepSeekModelPlugin, DeepSeekModelProvider
from codeatlas.config.settings import DeepSeekSettings
from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    ModelMessage,
    ModelRequest,
    ModelResponseFormat,
    ModelRole,
    ToolDefinition,
)
from codeatlas.domain.errors import ModelProviderError, ModelResponseError


def make_settings(**overrides: object) -> DeepSeekSettings:
    """Build enabled DeepSeek settings with deterministic retry behaviour."""
    values: dict[str, object] = {
        "enabled": True,
        "api_key": SecretStr("test-key-123"),
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "timeout_seconds": 5.0,
        "max_retries": 0,
        "retry_backoff_seconds": 0.0,
        "thinking": False,
    }
    values.update(overrides)
    return DeepSeekSettings(**values)  # type: ignore[arg-type]


def make_request(
    content: str = "Where is Client defined?",
    *,
    tools: tuple[ToolDefinition, ...] = (),
    response_format: ModelResponseFormat = ModelResponseFormat.TEXT,
) -> ModelRequest:
    """Build a minimal canonical request."""
    return ModelRequest(
        messages=(ModelMessage(role=ModelRole.USER, content=content),),
        tools=tools,
        response_format=response_format,
        max_output_tokens=256,
        temperature=0.2,
    )


def completion_payload(
    *,
    content: str | None = "OK",
    tool_calls: list[dict[str, object]] | None = None,
    finish_reason: str = "stop",
    reasoning_content: str | None = None,
) -> dict[str, object]:
    """Build an OpenAI-compatible chat completion payload."""
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


@pytest.mark.asyncio
async def test_text_completion_and_payload_shape() -> None:
    """The adapter posts the OpenAI-compatible payload and normalizes text."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=completion_payload())

    provider = DeepSeekModelProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await provider.complete(make_request())
    finally:
        await provider.aclose()

    assert response.content == "OK"
    assert response.model_id == "deepseek-flash"
    assert response.usage.prompt_tokens == 10
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-123"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-flash"
    assert payload["messages"] == [{"role": "user", "content": "Where is Client defined?"}]
    assert payload["stream"] is False
    assert payload["max_tokens"] == 256
    assert payload["thinking"] == {"type": "disabled"}
    assert "tools" not in payload


@pytest.mark.asyncio
async def test_thinking_and_json_mode_payload() -> None:
    """Thinking and JSON output are explicit provider-independent request switches."""
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(content='{"answer":"OK"}'))

    provider = DeepSeekModelProvider(
        make_settings(thinking=True),
        transport=httpx.MockTransport(handler),
    )
    try:
        await provider.complete(make_request(response_format=ModelResponseFormat.JSON))
    finally:
        await provider.aclose()

    assert bodies[0]["thinking"] == {"type": "enabled"}
    assert bodies[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_tool_call_arguments_are_normalized_from_json_string() -> None:
    """OpenAI tool call argument strings become validated canonical JSON."""
    tool = ToolDefinition(
        name="find_symbol",
        description="Find a symbol",
        input_schema_json='{"type":"object","properties":{"name":{"type":"string"}}}',
    )
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=completion_payload(
                content=None,
                tool_calls=[
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "find_symbol", "arguments": '{"name":"main"}'},
                    }
                ],
            ),
        )

    provider = DeepSeekModelProvider(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await provider.complete(make_request(tools=(tool,)))
    finally:
        await provider.aclose()

    assert response.tool_calls == (
        CanonicalToolCall(
            call_id="call_abc",
            tool_name="find_symbol",
            arguments_json='{"name":"main"}',
        ),
    )
    assert bodies[0]["tools"][0]["function"]["name"] == "find_symbol"
    assert bodies[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_rejected_safely() -> None:
    """Invalid tool argument JSON is a safe response error, never a raw exception."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=completion_payload(
                content=None,
                tool_calls=[
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "find_symbol", "arguments": "{not-json"},
                    }
                ],
            ),
        )

    provider = DeepSeekModelProvider(make_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelResponseError):
            await provider.complete(make_request())
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_reasoning_content_is_not_answer_content() -> None:
    """Thinking-mode reasoning is ignored and never leaks into the answer."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=completion_payload(content="final answer", reasoning_content="secret chain"),
        )

    provider = DeepSeekModelProvider(make_settings(), transport=httpx.MockTransport(handler))
    try:
        response = await provider.complete(make_request())
    finally:
        await provider.aclose()

    assert response.content == "final answer"
    assert "secret chain" not in response.content


@pytest.mark.asyncio
async def test_authentication_failure_never_leaks_key_or_prompt() -> None:
    """A 401 is a safe provider error with no credential or prompt material."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key test-key-123"}})

    provider = DeepSeekModelProvider(make_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelProviderError) as captured:
            await provider.complete(make_request("prompt-secret-value"))
    finally:
        await provider.aclose()

    text = f"{captured.value!s} {captured.value!r}"
    assert "test-key-123" not in text
    assert "prompt-secret-value" not in text
    assert "invalid key" not in text


@pytest.mark.asyncio
async def test_retryable_status_is_retried_then_succeeds() -> None:
    """A bounded retry recovers from a transient server error."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=completion_payload())

    provider = DeepSeekModelProvider(
        make_settings(max_retries=1),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await provider.complete(make_request())
    finally:
        await provider.aclose()

    assert response.content == "OK"
    assert calls == 2


@pytest.mark.asyncio
async def test_connect_timeout_is_mapped_to_safe_provider_error() -> None:
    """Transport timeouts never expose URLs or request payloads."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    provider = DeepSeekModelProvider(make_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ModelProviderError) as captured:
            await provider.complete(make_request("prompt-secret-value"))
    finally:
        await provider.aclose()

    text = f"{captured.value!s} {captured.value!r}"
    assert "test-key-123" not in text
    assert "prompt-secret-value" not in text


@pytest.mark.asyncio
async def test_disabled_provider_cannot_complete() -> None:
    """A disabled provider is a safe failure."""
    provider = DeepSeekModelProvider(make_settings(enabled=False, api_key=None))
    try:
        with pytest.raises(ModelProviderError):
            await provider.complete(make_request())
    finally:
        await provider.aclose()


def test_base_url_with_credentials_or_query_is_rejected() -> None:
    """Credentialed or query-bearing base URLs are configuration errors."""
    with pytest.raises(ValueError):
        DeepSeekModelProvider(make_settings(base_url="https://user:pass@api.deepseek.com"))
    with pytest.raises(ValueError):
        DeepSeekModelProvider(make_settings(base_url="https://api.deepseek.com?key=value"))


@pytest.mark.asyncio
async def test_plugin_probes_models_endpoint_and_closes() -> None:
    """The plugin reports availability from /models and releases its client."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    plugin = DeepSeekModelPlugin.from_settings(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    await plugin.start()

    assert plugin.available is True
    assert plugin.degraded_reason is None
    assert seen == ["https://api.deepseek.com/models"]

    await plugin.stop()
    assert plugin.provider.closed is True
    await plugin.stop()

    try:
        await plugin.provider.complete(make_request())
    except ModelProviderError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("closed provider must not complete requests")


@pytest.mark.asyncio
async def test_plugin_reports_degraded_when_unreachable() -> None:
    """An unreachable provider degrades instead of failing application startup."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    plugin = DeepSeekModelPlugin.from_settings(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    await plugin.start()
    try:
        assert plugin.available is False
        assert plugin.degraded_reason == "deepseek_unreachable"
    finally:
        await plugin.stop()


@pytest.mark.asyncio
async def test_plugin_disabled_without_network() -> None:
    """A disabled provider does not probe the network."""
    plugin = DeepSeekModelPlugin.from_settings(make_settings(enabled=False, api_key=None))
    await plugin.start()
    try:
        assert plugin.available is False
        assert plugin.degraded_reason == "provider_disabled"
    finally:
        await plugin.stop()


@pytest.mark.skipif(
    os.environ.get("CODEATLAS_TEST_DEEPSEEK") != "1" or not os.environ.get("DEEPSEEK_API_KEY"),
    reason="set CODEATLAS_TEST_DEEPSEEK=1 and DEEPSEEK_API_KEY to run",
)
@pytest.mark.asyncio
async def test_live_deepseek_completion() -> None:
    """Optional live smoke test against the real DeepSeek API."""
    settings = DeepSeekSettings(
        enabled=True,
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        max_retries=0,
        timeout_seconds=90.0,
        thinking=False,
    )
    provider = DeepSeekModelProvider.from_settings(settings)
    try:
        response = await provider.complete(
            ModelRequest(
                messages=(ModelMessage(role=ModelRole.USER, content="Reply with exactly: OK"),),
                max_output_tokens=64,
                temperature=0.0,
            )
        )
    finally:
        await provider.aclose()

    assert response.content.strip()
    assert response.model_id
