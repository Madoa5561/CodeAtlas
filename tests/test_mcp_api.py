"""MCP protocol handler and Web API tool endpoint tests."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

import httpx
import pytest

from codeatlas.adapters.http.api import ApiServices, create_api_app
from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    EvidenceCandidate,
    EvidenceKind,
    ToolDefinition,
    ToolOutcome,
)
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryPath,
)
from codeatlas.entrypoints.mcp import McpServer


class FakeExecutor:
    def catalog(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name="search_text",
                description="Search text.",
                input_schema_json=json.dumps(
                    {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    }
                ),
            ),
        )

    async def execute(self, call: CanonicalToolCall, context: object) -> ToolOutcome:
        assert call.tool_name == "search_text"
        return ToolOutcome(
            tool_name="search_text",
            ok=True,
            payload_json=json.dumps({"count": 1}),
            evidence=(
                EvidenceCandidate.create(
                    kind=EvidenceKind.FILE_EXCERPT,
                    repository_id=RepositoryId("repo_" + "a" * 32),
                    commit_sha="b" * 40,
                    path=RepositoryPath(PurePosixPath("src/app.ts")),
                    start_line=1,
                    end_line=2,
                    excerpt="hello",
                    retrieval_method="index_text_search",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_mcp_initialize_list_and_call() -> None:
    server = McpServer(FakeExecutor(), version="0.8.0")  # type: ignore[arg-type]
    initialized = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )
    assert initialized is not None
    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    assert initialized["result"]["serverInfo"]["name"] == "codeatlas"

    listed = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    assert listed["result"]["tools"][0]["name"] == "search_text"

    called = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_text", "arguments": {"query": "hello"}},
        }
    )
    assert called is not None
    body = called["result"]["structuredContent"]
    assert body["ok"] is True
    assert body["payload"]["count"] == 1
    assert body["evidence"][0]["retrieval_method"] == "index_text_search"
    assert called["result"]["isError"] is False


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_method_and_notifications() -> None:
    server = McpServer(FakeExecutor(), version="0.8.0")  # type: ignore[arg-type]
    assert await server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    unknown = await server.handle({"jsonrpc": "2.0", "id": 9, "method": "missing"})
    assert unknown is not None
    assert unknown["error"]["code"] == -32601


class FakeRegistry:
    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return ()


def make_api_services() -> ApiServices:
    return ApiServices(
        version="0.8.0",
        registry=FakeRegistry(),  # type: ignore[arg-type]
        interactions=None,
        webhook_store=None,  # type: ignore[arg-type]
        github_webhook_secret=None,
        allow_ask=False,
        max_body_bytes=10_000,
        tools=FakeExecutor(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_web_api_lists_and_calls_tools() -> None:
    app = create_api_app(make_api_services())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        listed = await client.get("/tools")
        called = await client.post(
            "/tools/call",
            json={"name": "search_text", "arguments": {"query": "hello"}},
        )
    assert listed.status_code == 200
    assert listed.json()["tools"][0]["name"] == "search_text"
    assert called.status_code == 200
    assert called.json()["ok"] is True
    assert called.json()["evidence"][0]["path"] == "src/app.ts"
