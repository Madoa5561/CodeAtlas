"""Ports for durable conversation memory, idempotency, and thread locking."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from codeatlas.domain.conversation_models import (
    ConversationId,
    ConversationMessage,
    InteractionClaim,
    InteractionOutcomeStatus,
)


class ConversationStorePort(Protocol):
    """Durable conversation state and idempotency boundary."""

    async def assert_schema_current(self) -> None:
        """Fail unless the persistent schema matches the packaged migrations."""
        ...

    async def try_claim_interaction(
        self,
        *,
        idempotency_key: str,
        conversation_id: ConversationId,
        now: datetime,
    ) -> InteractionClaim:
        """Atomically claim one idempotency key or report its prior state."""
        ...

    async def complete_interaction(
        self,
        *,
        idempotency_key: str,
        status: InteractionOutcomeStatus,
        text: str | None,
        now: datetime,
    ) -> None:
        """Record a terminal interaction outcome with an optional replayable response."""
        ...

    async def get_interaction_response(
        self, idempotency_key: str
    ) -> tuple[InteractionOutcomeStatus, str | None] | None:
        """Return a previously recorded terminal outcome for replay."""
        ...

    async def append_message(self, message: ConversationMessage) -> None:
        """Persist one bounded conversation turn."""
        ...

    async def load_history(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[ConversationMessage, ...]:
        """Return the newest bounded history in chronological order."""
        ...

    async def acquire_conversation_lock(
        self,
        *,
        conversation_id: ConversationId,
        owner_token: str,
        wait_seconds: float,
        lease_seconds: float,
        now: datetime,
    ) -> None:
        """Acquire an exclusive conversation lock or fail with a safe busy error."""
        ...

    async def release_conversation_lock(
        self, *, conversation_id: ConversationId, owner_token: str
    ) -> None:
        """Release an owned conversation lock; missing ownership is tolerated."""
        ...

    async def purge_history_before(self, cutoff: datetime) -> int:
        """Delete conversations and messages older than the configured retention."""
        ...

    async def close(self) -> None:
        """Release database resources owned by the adapter."""
        ...
