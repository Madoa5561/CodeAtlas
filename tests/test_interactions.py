"""Tests for the channel-independent interaction use case."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from codeatlas.application.interactions import InteractionApplicationService
from codeatlas.domain.agent_models import (
    AgentDiagnostics,
    AgentLimits,
    AgentOutcome,
    QueryContext,
)
from codeatlas.domain.conversation_models import (
    ConversationId,
    ConversationMessage,
    InteractionClaim,
    InteractionClaimStatus,
    InteractionOutcomeStatus,
    MessageRole,
)
from codeatlas.domain.errors import ConversationBusyError, IndexQueryError
from codeatlas.domain.interaction_models import (
    Actor,
    CanonicalInteraction,
    CanonicalResponse,
    ConversationRef,
    InboundMessage,
    InteractionCapabilities,
    InteractionKind,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)

_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


@dataclass
class InMemoryConversationStore:
    """Minimal in-memory conversation store with claim and lock semantics."""

    claims: dict[str, tuple[InteractionClaimStatus, str | None]] = field(default_factory=dict)
    messages: list[ConversationMessage] = field(default_factory=list)
    outcomes: dict[str, InteractionOutcomeStatus] = field(default_factory=dict)
    locks: set[ConversationId] = field(default_factory=set)
    busy: bool = False
    release_calls: int = 0

    async def assert_schema_current(self) -> None:
        """No-op in-memory schema check."""

    async def try_claim_interaction(
        self,
        *,
        idempotency_key: str,
        conversation_id: ConversationId,
        now: datetime,
    ) -> InteractionClaim:
        """Claim once and report later duplicate states."""
        if idempotency_key in self.claims:
            status, text = self.claims[idempotency_key]
            if status is InteractionClaimStatus.CLAIMED:
                return InteractionClaim(status=InteractionClaimStatus.ALREADY_IN_PROGRESS)
            return InteractionClaim(status=status, prior_response=text)
        self.claims[idempotency_key] = (InteractionClaimStatus.CLAIMED, None)
        return InteractionClaim(status=InteractionClaimStatus.CLAIMED)

    async def complete_interaction(
        self,
        *,
        idempotency_key: str,
        status: InteractionOutcomeStatus,
        text: str | None,
        now: datetime,
    ) -> None:
        """Record a terminal outcome and make it replayable."""
        self.outcomes[idempotency_key] = status
        claim_status = (
            InteractionClaimStatus.ALREADY_COMPLETED
            if status is InteractionOutcomeStatus.COMPLETED
            else InteractionClaimStatus.ALREADY_FAILED
        )
        self.claims[idempotency_key] = (claim_status, text)

    async def get_interaction_response(
        self, idempotency_key: str
    ) -> tuple[InteractionOutcomeStatus, str | None] | None:
        """Return a recorded outcome when one exists."""
        status = self.outcomes.get(idempotency_key)
        if status is None:
            return None
        return status, self.claims[idempotency_key][1]

    async def append_message(self, message: ConversationMessage) -> None:
        """Append one bounded conversation message."""
        self.messages.append(message)

    async def load_history(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[ConversationMessage, ...]:
        """Return newest messages chronologically."""
        matching = [
            message for message in self.messages if message.conversation_id == conversation_id
        ]
        return tuple(matching[-limit:])

    async def acquire_conversation_lock(
        self,
        *,
        conversation_id: ConversationId,
        owner_token: str,
        wait_seconds: float,
        lease_seconds: float,
        now: datetime,
    ) -> None:
        """Reject when the fixture marks the conversation busy."""
        if self.busy or conversation_id in self.locks:
            raise ConversationBusyError()
        self.locks.add(conversation_id)

    async def release_conversation_lock(
        self, *, conversation_id: ConversationId, owner_token: str
    ) -> None:
        """Release the fixture lock."""
        self.release_calls += 1
        self.locks.discard(conversation_id)

    async def purge_history_before(self, cutoff: datetime) -> int:
        """No-op retention purge for the fixture."""
        return 0

    async def close(self) -> None:
        """No-op close for the fixture."""


@dataclass
class FakeAgent:
    """Agent runner that returns a fixed response and records contexts."""

    response: CanonicalResponse
    contexts: list[QueryContext] = field(default_factory=list)
    error: Exception | None = None

    async def run(self, context: QueryContext, limits: AgentLimits) -> AgentOutcome:
        """Run the fake pipeline or raise the configured error."""
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return AgentOutcome(
            response=self.response,
            diagnostics=AgentDiagnostics(iterations=1, tool_calls=2, evidence_ranked=1),
        )


def make_response() -> CanonicalResponse:
    """Build a citation-free answer used by most tests."""
    return CanonicalResponse(
        status=ResponseStatus.OK,
        blocks=(ResponseBlock(kind=ResponseBlockKind.TEXT, text="answer"),),
        citations=(),
        confidence=0.8,
    )


def make_interaction(key: str = "discord:message-1") -> CanonicalInteraction:
    """Build a representative Discord forum interaction."""
    return CanonicalInteraction(
        interaction_id="discord_msg_1",
        idempotency_key=key,
        kind=InteractionKind.THREAD_MESSAGE,
        actor=Actor(actor_id="discord_user_1", display_name="user"),
        conversation=ConversationRef(
            conversation_id="discord_thread_1",
            source="discord",
            parent_id="discord_forum_1",
            is_thread=True,
        ),
        message=InboundMessage(
            message_id="discord_msg_1",
            content="Where is app?",
            created_at=_NOW,
        ),
        capabilities=InteractionCapabilities.discord_thread(),
        locale="ja",
        received_at=_NOW,
    )


def make_service(
    store: InMemoryConversationStore,
    agent: FakeAgent,
) -> InteractionApplicationService:
    """Build the service with deterministic time and limits."""
    return InteractionApplicationService(
        store=store,
        agent=agent,
        limits=AgentLimits(),
        history_limit=5,
        lock_wait_seconds=1.0,
        lock_lease_seconds=60.0,
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_interaction_processes_once_and_stores_history() -> None:
    """A claimed interaction runs the agent, stores both turns, and completes."""
    store = InMemoryConversationStore()
    agent = FakeAgent(response=make_response())
    service = make_service(store, agent)

    outcome = await service.handle(make_interaction())

    assert outcome.processed is True
    assert outcome.duplicate is False
    assert outcome.response is not None
    assert outcome.response.status is ResponseStatus.OK
    assert len(agent.contexts) == 1
    assert [message.role for message in store.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert store.outcomes["discord:message-1"] is InteractionOutcomeStatus.COMPLETED
    assert store.locks == set()
    assert store.release_calls == 1


@pytest.mark.asyncio
async def test_duplicate_interaction_is_not_reprocessed() -> None:
    """A replay of the same idempotency key returns the prior response without rerunning."""
    store = InMemoryConversationStore()
    agent = FakeAgent(response=make_response())
    service = make_service(store, agent)

    first = await service.handle(make_interaction())
    second = await service.handle(make_interaction())

    assert first.processed is True
    assert second.processed is False
    assert second.duplicate is True
    assert second.prior_response_text == "answer"
    assert len(agent.contexts) == 1
    assert len(store.messages) == 2


@pytest.mark.asyncio
async def test_duplicate_in_progress_interaction_is_ignored() -> None:
    """A second event for an in-flight interaction is not processed."""
    store = InMemoryConversationStore()
    agent = FakeAgent(response=make_response())
    service = make_service(store, agent)
    await store.try_claim_interaction(
        idempotency_key="discord:message-1",
        conversation_id=ConversationId.derive("discord", "discord_thread_1"),
        now=_NOW,
    )

    outcome = await service.handle(make_interaction())

    assert outcome.processed is False
    assert outcome.claim_status is InteractionClaimStatus.ALREADY_IN_PROGRESS
    assert len(agent.contexts) == 0


@pytest.mark.asyncio
async def test_busy_conversation_returns_safe_rejection() -> None:
    """Lock contention returns a safe busy response and records the outcome."""
    store = InMemoryConversationStore(busy=True)
    agent = FakeAgent(response=make_response())
    service = make_service(store, agent)

    outcome = await service.handle(make_interaction())

    assert outcome.processed is True
    assert outcome.response is not None
    assert outcome.response.status is ResponseStatus.REJECTED
    assert outcome.response.safe_error is not None
    assert "Traceback" not in outcome.response.plain_text
    assert len(agent.contexts) == 0
    assert store.outcomes["discord:message-1"] is InteractionOutcomeStatus.REJECTED


@pytest.mark.asyncio
async def test_known_agent_error_is_safely_reported() -> None:
    """A classified agent failure exposes only its safe message, never the cause."""
    store = InMemoryConversationStore()
    agent = FakeAgent(
        response=make_response(), error=IndexQueryError(internal_cause=RuntimeError("secret"))
    )
    service = make_service(store, agent)

    outcome = await service.handle(make_interaction())

    assert outcome.response is not None
    assert outcome.response.status is ResponseStatus.ERROR
    assert outcome.response.safe_error == IndexQueryError.default_safe_message
    assert "secret" not in outcome.response.plain_text
    assert store.outcomes["discord:message-1"] is InteractionOutcomeStatus.FAILED


@pytest.mark.asyncio
async def test_unexpected_agent_error_is_safely_reported() -> None:
    """An unexpected exception never leaks internals to the channel."""
    store = InMemoryConversationStore()
    agent = FakeAgent(response=make_response(), error=RuntimeError("internal detail"))
    service = make_service(store, agent)

    outcome = await service.handle(make_interaction())

    assert outcome.response is not None
    assert outcome.response.status is ResponseStatus.ERROR
    assert "internal detail" not in outcome.response.plain_text
    assert store.outcomes["discord:message-1"] is InteractionOutcomeStatus.FAILED


@pytest.mark.asyncio
async def test_history_is_loaded_without_the_current_question() -> None:
    """The agent receives prior history before the current turn is appended."""
    store = InMemoryConversationStore()
    conversation_id = ConversationId.derive("discord", "discord_thread_1")
    store.messages.append(
        ConversationMessage(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content="previous question",
            created_at=_NOW - timedelta(minutes=2),
        )
    )
    store.messages.append(
        ConversationMessage(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content="previous answer",
            created_at=_NOW - timedelta(minutes=1),
        )
    )
    agent = FakeAgent(response=make_response())
    service = make_service(store, agent)

    await service.handle(make_interaction())

    assert len(agent.contexts[0].history) == 2
    assert [message.content for message in agent.contexts[0].history] == [
        "previous question",
        "previous answer",
    ]
