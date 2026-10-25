"""Model Context Protocol stdio server exposing the read-only tool catalog."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TextIO

from codeatlas import __version__
from codeatlas.application.tools import ToolExecutor
from codeatlas.config.settings import load_settings
from codeatlas.domain.agent_models import CanonicalToolCall, ToolContext, ToolOutcome
from codeatlas.runtime import CodeAtlasRuntime

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_MAX_MESSAGE_BYTES = 1_000_000
_JSONRPC_VERSION = "2.0"


def _evidence_payload(outcome: ToolOutcome) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": item.evidence_id,
            "kind": item.kind.value,
            "repository_id": item.repository_id.value,
            "commit_sha": item.commit_sha,
            "path": item.path.as_posix(),
            "start_line": item.start_line,
            "end_line": item.end_line,
            "excerpt": item.excerpt,
            "retrieval_method": item.retrieval_method,
            "external_url": item.external_url,
        }
        for item in outcome.evidence
    ]


def _outcome_payload(outcome: ToolOutcome) -> dict[str, object]:
    try:
        payload: Any = json.loads(outcome.payload_json)
    except ValueError:
        payload = {}
    result: dict[str, object] = {
        "ok": outcome.ok,
        "payload": payload if isinstance(payload, dict) else {},
        "evidence": _evidence_payload(outcome),
        "truncated": outcome.truncated,
    }
    if not outcome.ok:
        result["error_category"] = (
            outcome.error_category.value if outcome.error_category is not None else None
        )
        result["safe_message"] = outcome.safe_message
    return result


class McpServer:
    """Minimal MCP JSON-RPC handler independent of transport."""

    def __init__(self, executor: ToolExecutor, *, version: str = __version__) -> None:
        self._executor = executor
        self._version = version
        self._counter = 0

    def _tool_definitions(self) -> list[dict[str, object]]:
        tools: list[dict[str, object]] = []
        for definition in self._executor.catalog():
            try:
                schema: Any = json.loads(definition.input_schema_json)
            except ValueError:
                schema = {"type": "object"}
            tools.append(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "inputSchema": schema if isinstance(schema, dict) else {"type": "object"},
                }
            )
        return tools

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC message, returning a response for requests."""
        message_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != _JSONRPC_VERSION or not isinstance(method, str):
            return self._error(message_id, -32600, "Invalid Request")
        is_notification = "id" not in message or message_id is None
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "initialize":
            if is_notification:
                return None
            params = message.get("params")
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            version = requested if isinstance(requested, str) and requested else _PROTOCOL_VERSION
            return self._result(
                message_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "codeatlas", "version": self._version},
                    "instructions": (
                        "Read-only code intelligence tools. Call tools/list, then tools/call "
                        "with a registered repository question."
                    ),
                },
            )
        if method == "ping":
            return self._result(message_id, {}) if not is_notification else None
        if method == "tools/list":
            if is_notification:
                return None
            return self._result(message_id, {"tools": self._tool_definitions()})
        if method == "tools/call":
            if is_notification:
                return None
            return await self._call_tool(message_id, message.get("params"))
        return self._error(message_id, -32601, "Method not found")

    async def _call_tool(self, message_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(message_id, -32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return self._error(message_id, -32602, "Invalid params")
        self._counter += 1
        call_id = f"mcp_{self._counter}"
        try:
            call = CanonicalToolCall(
                call_id=call_id,
                tool_name=name,
                arguments_json=json.dumps(arguments, ensure_ascii=False),
            )
        except ValueError:
            return self._error(message_id, -32602, "Invalid params")
        outcome = await self._executor.execute(
            call,
            ToolContext(request_id=f"mcp_request_{self._counter}"),
        )
        body = _outcome_payload(outcome)
        text = json.dumps(body, ensure_ascii=False, sort_keys=True)
        return self._result(
            message_id,
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": body,
                "isError": not outcome.ok,
            },
        )

    @staticmethod
    def _result(message_id: Any, result: dict[str, object]) -> dict[str, Any]:
        return {"jsonrpc": _JSONRPC_VERSION, "id": message_id, "result": result}

    @staticmethod
    def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": _JSONRPC_VERSION,
            "id": message_id,
            "error": {"code": code, "message": message},
        }


async def _serve_stdio(server: McpServer, *, input_stream: TextIO, output_stream: TextIO) -> None:
    while True:
        line = await asyncio.to_thread(input_stream.readline)
        if not line:
            return
        response: dict[str, Any] | None
        if len(line.encode("utf-8", errors="replace")) > _MAX_MESSAGE_BYTES:
            response = McpServer._error(None, -32600, "Message too large")
        else:
            try:
                decoded = json.loads(line)
            except ValueError:
                response = McpServer._error(None, -32700, "Parse error")
            else:
                if not isinstance(decoded, dict):
                    response = McpServer._error(None, -32600, "Invalid Request")
                else:
                    try:
                        response = await server.handle(decoded)
                    except Exception:
                        logger.exception("mcp_request_failed")
                        response = McpServer._error(decoded.get("id"), -32603, "Internal error")
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()


async def _run() -> None:
    settings = load_settings()
    runtime = CodeAtlasRuntime.create(
        settings,
        enable_discord=False,
        enable_api=False,
        log_stream=sys.stderr,
    )
    await runtime.start()
    try:
        server = McpServer(runtime.tool_executor)
        await _serve_stdio(server, input_stream=sys.stdin, output_stream=sys.stdout)
    finally:
        await runtime.stop()


def main() -> None:
    """Run the MCP server over stdio until EOF."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
