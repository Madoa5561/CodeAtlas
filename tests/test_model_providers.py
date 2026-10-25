"""External model provider adapter and selection tests."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from codeatlas.adapters.model.anthropic import AnthropicModelProvider
from codeatlas.adapters.model.gemini import GeminiModelProvider
from codeatlas.config.settings import (
    AnthropicSettings,
    CodeAtlasSettings,
    GeminiSettings,
    OllamaSettings,
    OpenAISettings,
)
from codeatlas.domain.agent_models import (
    ModelMessage,
    ModelRequest,
    ModelResponseFormat,
    ModelRole,
)
from codeatlas.domain.errors import ModelProviderError
from codeatlas.runtime import CodeAtlasRuntime


def request() -> ModelRequest:
    return ModelRequest(
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="system rules"),
            ModelMessage(role=ModelRole.USER, content="question"),
        ),
        temperature=0.1,
        max_output_tokens=500,
        response_format=ModelResponseFormat.JSON,
    )


@pytest.mark.asyncio
async def test_anthropic_messages_adapter_and_probe() -> None:
    calls: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        if http_request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"answer":"ok"}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 11, "output_tokens": 4},
            },
        )

    provider = AnthropicModelProvider(
        AnthropicSettings(enabled=True, api_key="key", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await provider.check_available() is True
        response = await provider.complete(request())
    finally:
        await provider.aclose()

    assert response.content == '{"answer":"ok"}'
    assert response.usage.prompt_tokens == 11
    assert response.usage.completion_tokens == 4
    post = next(call for call in calls if call.method == "POST")
    assert post.url.path == "/v1/messages"
    assert post.headers["x-api-key"] == "key"
    assert post.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(post.content)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 500
    assert "single valid JSON object" in body["system"]


@pytest.mark.asyncio
async def test_anthropic_rejects_auth_failure_safely() -> None:
    provider = AnthropicModelProvider(
        AnthropicSettings(enabled=True, api_key="key"),
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={})),
    )
    try:
        with pytest.raises(ModelProviderError):
            await provider.complete(request())
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_gemini_generate_content_adapter_and_probe() -> None:
    calls: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        if http_request.method == "GET":
            return httpx.Response(200, json={"name": "models/test"})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"answer":"ok"}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3},
            },
        )

    provider = GeminiModelProvider(
        GeminiSettings(enabled=True, api_key="key", model="test-model"),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await provider.check_available() is True
        response = await provider.complete(request())
    finally:
        await provider.aclose()

    assert response.content == '{"answer":"ok"}'
    assert response.finish_reason == "STOP"
    assert response.usage.prompt_tokens == 9
    post = next(call for call in calls if call.method == "POST")
    assert post.url.path == "/v1beta/models/test-model:generateContent"
    assert post.url.params["key"] == "key"
    body = json.loads(post.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "system rules"


def test_only_one_model_provider_can_be_enabled() -> None:
    with pytest.raises(ValidationError):
        CodeAtlasSettings(
            ollama=OllamaSettings(enabled=False),
            openai=OpenAISettings(enabled=True, api_key="one"),
            anthropic=AnthropicSettings(enabled=True, api_key="two"),
        )


def test_enabled_external_provider_requires_key() -> None:
    with pytest.raises(ValidationError):
        GeminiSettings(enabled=True)


@pytest.mark.asyncio
async def test_runtime_selects_each_external_provider_without_connecting() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}))
    for settings, expected in (
        (
            CodeAtlasSettings(
                _env_file=None,
                ollama=OllamaSettings(enabled=False),
                openai=OpenAISettings(enabled=True, api_key="key"),
            ),
            "openai",
        ),
        (
            CodeAtlasSettings(
                _env_file=None,
                ollama=OllamaSettings(enabled=False),
                anthropic=AnthropicSettings(enabled=True, api_key="key"),
            ),
            "anthropic",
        ),
        (
            CodeAtlasSettings(
                _env_file=None,
                ollama=OllamaSettings(enabled=False),
                gemini=GeminiSettings(enabled=True, api_key="key"),
            ),
            "gemini",
        ),
    ):
        runtime = CodeAtlasRuntime.create(
            settings,
            enable_discord=False,
            enable_api=False,
            model_transport=transport,
        )
        try:
            assert runtime.model_plugin.provider.provider_id == expected
        finally:
            await runtime.model_plugin.provider.aclose()
