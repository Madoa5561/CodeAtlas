"""SQLite conversation store, schema migration, idempotency, and locking tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from codeatlas.domain.conversation_models import (
    ConversationId,
    ConversationMessage,
    InteractionClaimStatus,
    InteractionOutcomeStatus,
    MessageRole,
    MessageStatus,
)
from codeatlas.domain.errors import ConversationBusyError, ConversationStoreError
from codeatlas.infrastructure.conversation.store import SqliteConversationStore
from codeatlas.infrastructure.indexing.migrations import (
    current_revision,
    downgrade_database,
    upgrade_database,
)
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION

_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
_CONVERSATION = ConversationId("conv_" + "a" * 32)
_OTHER_CONVERSATION = ConversationId("conv_" + "b" * 32)
_CONVERSATION_TABLES = {
    "conversations",
    "conversation_messages",
    "interaction_records",
    "conversation_locks",
}


def database_url(path: Path) -> str:
    """Return a portable absolute SQLite URL for a temporary path."""
    return f"sqlite:///{path.as_posix()}"


def table_names(path: Path) -> set[str]:
    """Read table names without leaking the temporary engine."""
    engine = create_engine(database_url(path))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def conversation_message(
    content: str,
    *,
    created_at: datetime,
    conversation_id: ConversationId = _CONVERSATION,
    role: MessageRole = MessageRole.USER,
    status: MessageStatus = MessageStatus.OK,
    source_message_id: str | None = None,
    correlation_id: str = "discord:message",
) -> ConversationMessage:
    """Build one bounded domain message for store tests."""
    return ConversationMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        created_at=created_at,
        source_message_id=source_message_id,
        status=status,
        correlation_id=correlation_id,
    )


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[SqliteConversationStore, Path]]:
    """Create a migrated conversation database and dispose its engine."""
    path = tmp_path / "conversation.db"
    upgrade_database(database_url(path))
    store = SqliteConversationStore(database_url(path))
    try:
        yield store, path
    finally:
        store.engine.dispose()


def test_schema_migration_lifecycle(tmp_path: Path) -> None:
    """Upgrade, downgrade, and re-upgrade expose only the intended schema."""
    path = tmp_path / "conversation.db"
    url = database_url(path)
    upgrade_database(url)
    assert current_revision(url) == SCHEMA_REVISION
    assert _CONVERSATION_TABLES <= table_names(path)
    downgrade_database(url)
    assert current_revision(url) is None
    assert not (_CONVERSATION_TABLES & table_names(path))
    upgrade_database(url)
    assert current_revision(url) == SCHEMA_REVISION
    assert _CONVERSATION_TABLES <= table_names(path)


@pytest.mark.asyncio
async def test_store_requires_current_schema(tmp_path: Path) -> None:
    """Missing or stale conversation schemas fail with safe errors."""
    missing_path = tmp_path / "missing.db"
    missing = SqliteConversationStore(database_url(missing_path))
    try:
        with pytest.raises(ConversationStoreError) as raised:
            await missing.assert_schema_current()
        assert str(missing_path) not in raised.value.safe_message
    finally:
        await missing.close()

    path = tmp_path / "stale.db"
    url = database_url(path)
    upgrade_database(url)
    downgrade_database(url, "0001_m3_transactional_index")
    stale = SqliteConversationStore(url)
    try:
        with pytest.raises(ConversationStoreError):
            await stale.assert_schema_current()
    finally:
        await stale.close()


@pytest.mark.asyncio
async def test_claim_duplicate_replay_and_failure(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """Idempotency claims converge to a single processing owner and replay prior text."""
    store, _ = database
    first = await store.try_claim_interaction(
        idempotency_key="discord:1", conversation_id=_CONVERSATION, now=_NOW
    )
    assert first.status is InteractionClaimStatus.CLAIMED
    assert first.should_process
    assert first.prior_response is None
    assert await store.get_interaction_response("discord:1") is None

    duplicate = await store.try_claim_interaction(
        idempotency_key="discord:1",
        conversation_id=_CONVERSATION,
        now=_NOW + timedelta(seconds=1),
    )
    assert duplicate.status is InteractionClaimStatus.ALREADY_IN_PROGRESS
    assert not duplicate.should_process

    await store.complete_interaction(
        idempotency_key="discord:1",
        status=InteractionOutcomeStatus.COMPLETED,
        text="answer text",
        now=_NOW + timedelta(seconds=2),
    )
    assert await store.get_interaction_response("discord:1") == (
        InteractionOutcomeStatus.COMPLETED,
        "answer text",
    )
    replay = await store.try_claim_interaction(
        idempotency_key="discord:1",
        conversation_id=_CONVERSATION,
        now=_NOW + timedelta(seconds=3),
    )
    assert replay.status is InteractionClaimStatus.ALREADY_COMPLETED
    assert replay.prior_response == "answer text"
    assert not replay.should_process

    await store.complete_interaction(
        idempotency_key="discord:1",
        status=InteractionOutcomeStatus.FAILED,
        text="late failure",
        now=_NOW + timedelta(seconds=4),
    )
    assert await store.get_interaction_response("discord:1") == (
        InteractionOutcomeStatus.COMPLETED,
        "answer text",
    )

    assert (
        await store.try_claim_interaction(
            idempotency_key="discord:2", conversation_id=_CONVERSATION, now=_NOW
        )
    ).should_process
    await store.complete_interaction(
        idempotency_key="discord:2",
        status=InteractionOutcomeStatus.FAILED,
        text="safe failure",
        now=_NOW + timedelta(seconds=1),
    )
    failed = await store.try_claim_interaction(
        idempotency_key="discord:2",
        conversation_id=_CONVERSATION,
        now=_NOW + timedelta(seconds=2),
    )
    assert failed.status is InteractionClaimStatus.ALREADY_FAILED
    assert failed.prior_response == "safe failure"

    assert (
        await store.try_claim_interaction(
            idempotency_key="discord:3", conversation_id=_CONVERSATION, now=_NOW
        )
    ).should_process
    await store.complete_interaction(
        idempotency_key="discord:3",
        status=InteractionOutcomeStatus.REJECTED,
        text=None,
        now=_NOW + timedelta(seconds=1),
    )
    rejected = await store.try_claim_interaction(
        idempotency_key="discord:3",
        conversation_id=_CONVERSATION,
        now=_NOW + timedelta(seconds=2),
    )
    assert rejected.status is InteractionClaimStatus.ALREADY_FAILED
    assert rejected.prior_response is None
    assert await store.get_interaction_response("discord:3") == (
        InteractionOutcomeStatus.REJECTED,
        None,
    )

    with pytest.raises(ConversationStoreError):
        await store.complete_interaction(
            idempotency_key="discord:unknown",
            status=InteractionOutcomeStatus.COMPLETED,
            text=None,
            now=_NOW,
        )


@pytest.mark.asyncio
async def test_expired_claim_is_reclaimed(tmp_path: Path) -> None:
    """An in-progress claim past its lease is recovered after a crash."""
    path = tmp_path / "conversation.db"
    upgrade_database(database_url(path))
    store = SqliteConversationStore(database_url(path), claim_lease_seconds=30.0)
    try:
        first = await store.try_claim_interaction(
            idempotency_key="discord:crash", conversation_id=_CONVERSATION, now=_NOW
        )
        assert first.status is InteractionClaimStatus.CLAIMED
        active = await store.try_claim_interaction(
            idempotency_key="discord:crash",
            conversation_id=_CONVERSATION,
            now=_NOW + timedelta(seconds=10),
        )
        assert active.status is InteractionClaimStatus.ALREADY_IN_PROGRESS
        recovered = await store.try_claim_interaction(
            idempotency_key="discord:crash",
            conversation_id=_CONVERSATION,
            now=_NOW + timedelta(seconds=31),
        )
        assert recovered.status is InteractionClaimStatus.CLAIMED
        again = await store.try_claim_interaction(
            idempotency_key="discord:crash",
            conversation_id=_CONVERSATION,
            now=_NOW + timedelta(seconds=32),
        )
        assert again.status is InteractionClaimStatus.ALREADY_IN_PROGRESS
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_claims_are_atomic(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """Only one concurrent caller wins the same idempotency key."""
    store, _ = database
    claims = await asyncio.gather(
        *(
            store.try_claim_interaction(
                idempotency_key="discord:race",
                conversation_id=_CONVERSATION,
                now=_NOW,
            )
            for _ in range(4)
        )
    )
    assert sum(claim.should_process for claim in claims) == 1
    assert all(
        claim.status in {InteractionClaimStatus.CLAIMED, InteractionClaimStatus.ALREADY_IN_PROGRESS}
        for claim in claims
    )


@pytest.mark.asyncio
async def test_history_is_bounded_and_chronological(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """History returns the newest bounded turns in chronological order."""
    store, _ = database
    for index in range(5):
        await store.append_message(
            conversation_message(
                f"message-{index}",
                created_at=_NOW + timedelta(seconds=index),
                conversation_id=_CONVERSATION,
                role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                status=MessageStatus.OK,
                source_message_id=f"source-{index}",
                correlation_id=f"discord:{index}",
            )
        )
    history = await store.load_history(_CONVERSATION, 3)
    assert tuple(message.content for message in history) == (
        "message-2",
        "message-3",
        "message-4",
    )
    assert all(message.created_at.tzinfo is not None for message in history)
    assert history[0].source_message_id == "source-2"
    assert history[1].role is MessageRole.ASSISTANT
    full = await store.load_history(_CONVERSATION, 10)
    assert [message.content for message in full] == [f"message-{index}" for index in range(5)]
    assert await store.load_history(_OTHER_CONVERSATION, 5) == ()
    with pytest.raises(ValueError):
        await store.load_history(_CONVERSATION, 0)


@pytest.mark.asyncio
async def test_lock_contention_wait_and_release(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """Conversation locks reject concurrent owners, wait, and release by owner."""
    store, _ = database
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-a",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW,
    )
    with pytest.raises(ConversationBusyError):
        await store.acquire_conversation_lock(
            conversation_id=_CONVERSATION,
            owner_token="owner-b",
            wait_seconds=0.05,
            lease_seconds=30.0,
            now=_NOW,
        )

    async def release_after_delay() -> None:
        await asyncio.sleep(0.02)
        await store.release_conversation_lock(conversation_id=_CONVERSATION, owner_token="owner-a")

    releaser = asyncio.create_task(release_after_delay())
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-b",
        wait_seconds=1.0,
        lease_seconds=30.0,
        now=_NOW,
    )
    await releaser

    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-b",
        wait_seconds=0.0,
        lease_seconds=60.0,
        now=_NOW + timedelta(seconds=1),
    )
    await store.release_conversation_lock(conversation_id=_CONVERSATION, owner_token="owner-a")
    with pytest.raises(ConversationBusyError):
        await store.acquire_conversation_lock(
            conversation_id=_CONVERSATION,
            owner_token="owner-a",
            wait_seconds=0.0,
            lease_seconds=30.0,
            now=_NOW + timedelta(seconds=2),
        )
    await store.release_conversation_lock(conversation_id=_CONVERSATION, owner_token="owner-b")
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-a",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW + timedelta(seconds=2),
    )


@pytest.mark.asyncio
async def test_lock_lease_takeover_is_owner_scoped(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """Only an expired lease may be taken over by a different owner."""
    store, _ = database
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-a",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW,
    )
    with pytest.raises(ConversationBusyError):
        await store.acquire_conversation_lock(
            conversation_id=_CONVERSATION,
            owner_token="owner-b",
            wait_seconds=0.0,
            lease_seconds=30.0,
            now=_NOW + timedelta(seconds=29),
        )
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-b",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW + timedelta(seconds=31),
    )
    with pytest.raises(ConversationBusyError):
        await store.acquire_conversation_lock(
            conversation_id=_CONVERSATION,
            owner_token="owner-a",
            wait_seconds=0.0,
            lease_seconds=30.0,
            now=_NOW + timedelta(seconds=32),
        )
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="owner-c",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW + timedelta(seconds=61),
    )


@pytest.mark.asyncio
async def test_purge_history_before_cutoff(
    database: tuple[SqliteConversationStore, Path],
) -> None:
    """Retention purges stale conversations, messages, records, and locks."""
    store, _ = database
    old = _NOW - timedelta(days=40)
    await store.append_message(
        conversation_message(
            "old question",
            created_at=old,
            source_message_id="111",
            correlation_id="discord:old",
        )
    )
    await store.append_message(
        conversation_message(
            "old answer",
            created_at=old + timedelta(seconds=1),
            role=MessageRole.ASSISTANT,
            correlation_id="discord:old",
        )
    )
    await store.try_claim_interaction(
        idempotency_key="discord:old", conversation_id=_CONVERSATION, now=old
    )
    await store.complete_interaction(
        idempotency_key="discord:old",
        status=InteractionOutcomeStatus.COMPLETED,
        text="old answer",
        now=old + timedelta(seconds=2),
    )
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="stale-owner",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=old,
    )
    await store.append_message(
        conversation_message(
            "recent message",
            created_at=_NOW,
            conversation_id=_OTHER_CONVERSATION,
            correlation_id="discord:recent",
        )
    )

    cutoff = _NOW - timedelta(days=30)
    assert await store.purge_history_before(cutoff) == 1
    assert await store.load_history(_CONVERSATION, 10) == ()
    assert await store.get_interaction_response("discord:old") is None
    assert [message.content for message in await store.load_history(_OTHER_CONVERSATION, 10)] == [
        "recent message"
    ]
    assert await store.purge_history_before(cutoff) == 0
    await store.acquire_conversation_lock(
        conversation_id=_CONVERSATION,
        owner_token="fresh-owner",
        wait_seconds=0.0,
        lease_seconds=30.0,
        now=_NOW,
    )


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path: Path) -> None:
    """Closing the store twice never raises."""
    path = tmp_path / "conversation.db"
    upgrade_database(database_url(path))
    store = SqliteConversationStore(database_url(path))
    await store.close()
    await store.close()
