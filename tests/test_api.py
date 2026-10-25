"""Contract tests for the local FastAPI interaction and webhook routes."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from codeatlas.adapters.http.api import ApiServices, create_api_app, verify_github_signature
from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
)


@dataclass
class FakeRegistry:
    """Registry double exposing a deterministic repository catalog."""

    descriptors: tuple[RepositoryDescriptor, ...] = ()

    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return self.descriptors


@dataclass
class FakeInteractions:
    """Interaction service double recording repository scopes."""

    response: CanonicalResponse
    scopes: list[tuple[RepositoryId, ...]] = field(default_factory=list)

    async def handle(
        self,
        interaction: object,
        *,
        repository_scope: tuple[RepositoryId, ...] = (),
    ) -> object:
        self.scopes.append(repository_scope)
        from codeatlas.application.interactions import InteractionOutcome
        from codeatlas.domain.conversation_models import (
            ConversationId,
            InteractionClaimStatus,
        )

        return InteractionOutcome(
            processed=True,
            duplicate=False,
            conversation_id=ConversationId.derive("api", "test"),
            claim_status=InteractionClaimStatus.CLAIMED,
            response=self.response,
        )


@dataclass
class FakeWebhookStore:
    """In-memory delivery ledger double."""

    seen: set[str] = field(default_factory=set)

    async def record_delivery(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        repository: str | None,
    ) -> bool:
        fresh = delivery_id not in self.seen
        self.seen.add(delivery_id)
        return fresh


def make_response() -> CanonicalResponse:
    return CanonicalResponse(
        status=ResponseStatus.OK,
        blocks=(ResponseBlock(kind=ResponseBlockKind.TEXT, text="回答です。"),),
        citations=(),
        confidence=0.8,
    )


def make_services(
    *,
    allow_ask: bool = True,
    secret: str | None = "webhook-secret",
    descriptors: tuple[RepositoryDescriptor, ...] = (),
) -> tuple[ApiServices, FakeInteractions, FakeWebhookStore]:
    interactions = FakeInteractions(response=make_response())
    store = FakeWebhookStore()
    services = ApiServices(
        version="0.2.0",
        registry=FakeRegistry(descriptors),
        interactions=interactions,
        webhook_store=store,
        github_webhook_secret=secret,
        allow_ask=allow_ask,
        max_body_bytes=10_000,
    )
    return services, interactions, store


@pytest.fixture
def app_client() -> httpx.AsyncClient:
    raise NotImplementedError


def client_for(services: ApiServices) -> httpx.AsyncClient:
    app = create_api_app(services)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def descriptor() -> RepositoryDescriptor:
    return RepositoryDescriptor(
        repository_id=RepositoryId("repo_" + "a" * 32),
        canonical_location=RepositoryLocation(Path(".")),
        name="linejs",
        default_branch="main",
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_health_and_repositories() -> None:
    services, _, _ = make_services(descriptors=(descriptor(),))
    async with client_for(services) as client:
        health = await client.get("/healthz")
        repositories = await client.get("/repositories")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["repositories"] == 1
    assert repositories.json()[0]["name"] == "linejs"


@pytest.mark.asyncio
async def test_ask_returns_canonical_response_and_scope() -> None:
    services, interactions, _ = make_services(descriptors=(descriptor(),))
    async with client_for(services) as client:
        response = await client.post(
            "/ask",
            json={
                "question": "Client はどこ?",
                "repository_id": descriptor().repository_id.value,
                "locale": "ja",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["response"]["status"] == "ok"
    assert payload["response"]["blocks"][0]["text"] == "回答です。"
    assert interactions.scopes == [((descriptor().repository_id),)]


@pytest.mark.asyncio
async def test_ask_rejects_unknown_repository() -> None:
    services, interactions, _ = make_services()
    async with client_for(services) as client:
        response = await client.post(
            "/ask",
            json={"question": "question", "repository_id": "repo_" + "b" * 32},
        )

    assert response.status_code == 404
    assert interactions.scopes == []


@pytest.mark.asyncio
async def test_ask_can_be_disabled() -> None:
    services, _, _ = make_services(allow_ask=False)
    async with client_for(services) as client:
        response = await client.post("/ask", json={"question": "question"})
    assert response.status_code == 403


def webhook_headers(body: bytes, secret: str, delivery: str = "delivery-1") -> dict[str, str]:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": f"sha256={signature}",
        "x-github-delivery": delivery,
        "x-github-event": "push",
        "content-type": "application/json",
    }


@pytest.mark.asyncio
async def test_webhook_verification_and_idempotency() -> None:
    services, _, store = make_services()
    body = json.dumps({"repository": {"full_name": "evex-dev/linejs"}}).encode()
    async with client_for(services) as client:
        first = await client.post(
            "/webhooks/github", content=body, headers=webhook_headers(body, "webhook-secret")
        )
        second = await client.post(
            "/webhooks/github", content=body, headers=webhook_headers(body, "webhook-secret")
        )

    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.status_code == 202
    assert second.json()["duplicate"] is True
    assert store.seen == {"delivery-1"}


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature() -> None:
    services, _, store = make_services()
    body = b'{"repository":{"full_name":"evex-dev/linejs"}}'
    bad_headers = webhook_headers(body, "wrong-secret")
    async with client_for(services) as client:
        response = await client.post("/webhooks/github", content=body, headers=bad_headers)

    assert response.status_code == 401
    assert store.seen == set()


@pytest.mark.asyncio
async def test_webhook_rejects_missing_secret_configuration() -> None:
    services, _, _ = make_services(secret=None)
    body = b"{}"
    async with client_for(services) as client:
        response = await client.post("/webhooks/github", content=body)
    assert response.status_code == 503


def test_signature_helper_rejects_malformed_headers() -> None:
    body = b"payload"
    assert verify_github_signature(body, None, "secret") is False
    assert verify_github_signature(body, "sha1=abc", "secret") is False
    assert verify_github_signature(body, "sha256=deadbeef", "secret") is False
    valid = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_github_signature(body, f"sha256={valid}", "secret") is True
