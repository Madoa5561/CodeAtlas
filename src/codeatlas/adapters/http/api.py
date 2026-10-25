"""FastAPI interaction API and GitHub webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from codeatlas.domain.agent_models import CanonicalToolCall, ToolContext
from codeatlas.domain.conversation_models import ConversationId
from codeatlas.domain.errors import CodeAtlasError
from codeatlas.domain.interaction_models import (
    Actor,
    CanonicalInteraction,
    ConversationRef,
    InboundMessage,
    InteractionCapabilities,
    InteractionKind,
)
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryId
from codeatlas.domain.repository_ports import RepositoryRegistryPort

_VERSION = "0.2.0"


class AskRequest(BaseModel):
    """Bounded question payload accepted by the local API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=20_000)
    repository_id: str | None = Field(
        default=None,
        pattern=r"^repo_[0-9a-f]{32}$",
        max_length=64,
    )
    locale: str = Field(default="ja", min_length=1, max_length=16)


class ToolCallRequest(BaseModel):
    """Validated tool invocation accepted by the local API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    repository_id: str | None = Field(
        default=None,
        pattern=r"^repo_[0-9a-f]{32}$",
        max_length=64,
    )


class WebhookStorePort(Protocol):
    """Minimal durable idempotency surface used by the webhook route."""

    async def record_delivery(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        repository: str | None,
    ) -> bool: ...


class WebhookSyncPort(Protocol):
    """Background index trigger used after a fresh push delivery."""

    async def request(
        self,
        *,
        event: str,
        repository: str | None,
        delivery_id: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Collaborators required by the API routes."""

    version: str
    registry: RepositoryRegistryPort
    interactions: Any
    webhook_store: WebhookStorePort
    github_webhook_secret: str | None
    allow_ask: bool
    max_body_bytes: int
    webhook_sync: WebhookSyncPort | None = None
    tools: Any | None = None


def verify_github_signature(body: bytes, header: str | None, secret: str) -> bool:
    """Verify an X-Hub-Signature-256 header without exposing the secret."""
    if not header or not header.startswith("sha256=") or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def _response_payload(outcome: Any) -> dict[str, object]:
    response = outcome.response
    if response is None:
        return {"duplicate": True, "response": None}
    return {
        "duplicate": outcome.duplicate,
        "response": {
            "status": response.status.value,
            "confidence": response.confidence,
            "blocks": [
                {
                    "kind": block.kind.value,
                    "text": block.text,
                    "language": block.language,
                }
                for block in response.blocks
            ],
            "citations": [
                {
                    "citation_id": citation.citation_id,
                    "repository_id": citation.repository_id,
                    "commit_sha": citation.commit_sha,
                    "path": citation.path,
                    "start_line": citation.start_line,
                    "end_line": citation.end_line,
                    "symbol_name": citation.symbol_name,
                    "retrieval_method": citation.retrieval_method,
                    "external_url": citation.external_url,
                }
                for citation in response.citations
            ],
            "uncertainties": list(response.uncertainties),
            "search_methods": list(response.search_methods),
            "follow_ups": list(response.follow_ups),
            "safe_error": response.safe_error,
        },
    }


def _descriptor_payload(descriptor: RepositoryDescriptor) -> dict[str, object]:
    return {
        "repository_id": descriptor.repository_id.value,
        "name": descriptor.name,
        "default_branch": descriptor.default_branch,
        "registered_at": descriptor.registered_at.isoformat(),
    }


def create_api_app(services: ApiServices) -> FastAPI:
    """Build the local API application without starting a server."""
    app = FastAPI(title="CodeAtlas API", version=services.version, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "version": services.version,
            "repositories": len(services.registry.list_all()),
        }

    @app.get("/repositories")
    async def repositories() -> list[dict[str, object]]:
        return [_descriptor_payload(item) for item in services.registry.list_all()]

    @app.get("/tools")
    async def tools() -> JSONResponse:
        if services.tools is None:
            return JSONResponse(
                status_code=503,
                content={"code": "tools_disabled", "message": "The tool API is disabled."},
            )
        definitions = []
        for definition in services.tools.catalog():
            try:
                schema: Any = json.loads(definition.input_schema_json)
            except ValueError:
                schema = {"type": "object"}
            definitions.append(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "input_schema": schema,
                    "version": definition.version,
                }
            )
        return JSONResponse(status_code=200, content={"tools": definitions})

    @app.post("/tools/call")
    async def call_tool(request: ToolCallRequest) -> JSONResponse:
        if services.tools is None:
            return JSONResponse(
                status_code=503,
                content={"code": "tools_disabled", "message": "The tool API is disabled."},
            )
        scope: tuple[RepositoryId, ...] = ()
        if request.repository_id is not None:
            repository_id = RepositoryId(request.repository_id)
            registered = {item.repository_id for item in services.registry.list_all()}
            if repository_id not in registered:
                return JSONResponse(
                    status_code=404,
                    content={"code": "not_found", "message": "The repository is not registered."},
                )
            scope = (repository_id,)
        token = secrets.token_hex(8)
        try:
            call = CanonicalToolCall(
                call_id=f"api_tool_{token}",
                tool_name=request.name,
                arguments_json=json.dumps(request.arguments, ensure_ascii=False),
            )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"code": "invalid_arguments", "message": "The tool arguments are invalid."},
            )
        outcome = await services.tools.execute(
            call,
            ToolContext(
                request_id=f"api_request_{token}",
                allowed_repository_ids=scope,
            ),
        )
        try:
            payload: Any = json.loads(outcome.payload_json)
        except ValueError:
            payload = {}
        return JSONResponse(
            status_code=200,
            content={
                "ok": outcome.ok,
                "payload": payload if isinstance(payload, dict) else {},
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "kind": item.kind.value,
                        "repository_id": item.repository_id.value,
                        "commit_sha": item.commit_sha,
                        "path": item.path.as_posix(),
                        "start_line": item.start_line,
                        "end_line": item.end_line,
                        "retrieval_method": item.retrieval_method,
                        "external_url": item.external_url,
                    }
                    for item in outcome.evidence
                ],
                "error_category": (
                    outcome.error_category.value if outcome.error_category is not None else None
                ),
                "safe_message": outcome.safe_message,
                "truncated": outcome.truncated,
            },
        )

    @app.post("/ask")
    async def ask(request: AskRequest) -> JSONResponse:
        if not services.allow_ask:
            return JSONResponse(
                status_code=403,
                content={"code": "forbidden", "message": "The ask endpoint is disabled."},
            )
        scope: tuple[RepositoryId, ...] = ()
        if request.repository_id is not None:
            repository_id = RepositoryId(request.repository_id)
            registered = {item.repository_id for item in services.registry.list_all()}
            if repository_id not in registered:
                return JSONResponse(
                    status_code=404,
                    content={"code": "not_found", "message": "The repository is not registered."},
                )
            scope = (repository_id,)
        token = secrets.token_hex(16)
        conversation_id = ConversationId.derive("api", token)
        now = datetime.now(UTC)
        interaction = CanonicalInteraction(
            interaction_id=f"api_msg_{token}",
            idempotency_key=f"api:{token}",
            kind=InteractionKind.CLI,
            actor=Actor(actor_id="api_client_1", display_name="api"),
            conversation=ConversationRef(
                conversation_id=conversation_id.value,
                source="api",
                is_thread=False,
            ),
            message=InboundMessage(
                message_id=f"api_message_{token}",
                content=request.question,
                created_at=now,
            ),
            capabilities=InteractionCapabilities.plain_cli(),
            locale=request.locale,
            received_at=now,
        )
        try:
            outcome = await services.interactions.handle(
                interaction,
                repository_scope=scope,
            )
        except CodeAtlasError as error:
            return JSONResponse(
                status_code=503,
                content={"code": error.code, "message": error.safe_message},
            )
        return JSONResponse(status_code=200, content=_response_payload(outcome))

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > services.max_body_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "code": "payload_too_large",
                    "message": "The webhook payload is too large.",
                },
            )
        secret = services.github_webhook_secret
        if not secret:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "webhook_disabled",
                    "message": "The GitHub webhook secret is not configured.",
                },
            )
        if not verify_github_signature(
            body,
            request.headers.get("x-hub-signature-256"),
            secret,
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "invalid_signature",
                    "message": "The webhook signature is invalid.",
                },
            )
        delivery_id = request.headers.get("x-github-delivery")
        if not delivery_id:
            return JSONResponse(
                status_code=400,
                content={"code": "missing_delivery", "message": "The delivery ID is missing."},
            )
        event = request.headers.get("x-github-event", "unknown")
        action: str | None = None
        repository: str | None = None
        try:
            payload_value: Any = json.loads(body)
        except ValueError:
            payload_value = {}
        if isinstance(payload_value, dict):
            raw_action = payload_value.get("action")
            if isinstance(raw_action, str):
                action = raw_action
            raw_repository = payload_value.get("repository")
            if isinstance(raw_repository, dict):
                raw_name = raw_repository.get("full_name")
                if isinstance(raw_name, str):
                    repository = raw_name
        fresh = await services.webhook_store.record_delivery(
            delivery_id=delivery_id,
            event=event,
            action=action,
            repository=repository,
        )
        indexing = "duplicate"
        if fresh and services.webhook_sync is not None:
            indexing = await services.webhook_sync.request(
                event=event,
                repository=repository,
                delivery_id=delivery_id,
            )
        elif fresh:
            indexing = "disabled"
        return JSONResponse(
            status_code=202,
            content={
                "delivery_id": delivery_id,
                "event": event,
                "accepted": True,
                "duplicate": not fresh,
                "indexing": indexing,
            },
        )

    return app
