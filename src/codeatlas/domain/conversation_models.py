"""Framework-independent conversation and interaction-ledger models."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

_CONVERSATION_ID = re.compile(r"^conv_[0-9a-f]{32}$")
_OPAQUE_KEY = re.compile(r"^[\w:.\-]{1,200}$")
_MAX_MESSAGE_CHARS = 100_000
_MAX_STORED_RESPONSE_CHARS = 200_000


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ConversationId:
    """Stable opaque identity for one conversation scope."""

    value: str

    @classmethod
    def derive(cls, source: str, external_id: str) -> ConversationId:
        """Derive a deterministic conversation ID from its source and external scope."""
        payload = f"{source}\x00{external_id}".encode()
        return cls(f"conv_{hashlib.sha256(payload).hexdigest()[:32]}")

    def __post_init__(self) -> None:
        if _CONVERSATION_ID.fullmatch(self.value) is None:
            raise ValueError("invalid conversation ID")

    def __str__(self) -> str:
        return self.value


class MessageRole(StrEnum):
    """Conversation author role persisted for memory and audit."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    """Outcome classification for one stored conversation message."""

    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One bounded conversation turn stored without credentials or attachments."""

    conversation_id: ConversationId
    role: MessageRole
    content: str
    created_at: datetime
    source_message_id: str | None = None
    status: MessageStatus = MessageStatus.OK
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("conversation message content is required")
        if len(self.content) > _MAX_MESSAGE_CHARS:
            raise ValueError("conversation message is too long")
        _require_aware(self.created_at)
        if self.source_message_id is not None and not self.source_message_id.strip():
            raise ValueError("source message ID must not be blank")
        if self.correlation_id is not None and (_OPAQUE_KEY.fullmatch(self.correlation_id) is None):
            raise ValueError("invalid correlation ID")


class InteractionClaimStatus(StrEnum):
    """Idempotency claim outcome for one inbound interaction."""

    CLAIMED = "claimed"
    ALREADY_COMPLETED = "already_completed"
    ALREADY_IN_PROGRESS = "already_in_progress"
    ALREADY_FAILED = "already_failed"


@dataclass(frozen=True, slots=True)
class InteractionClaim:
    """Result of atomically claiming an idempotency key."""

    status: InteractionClaimStatus
    prior_response: str | None = None

    def __post_init__(self) -> None:
        if (
            self.prior_response is not None
            and len(self.prior_response) > _MAX_STORED_RESPONSE_CHARS
        ):
            raise ValueError("stored response is too long")

    @property
    def should_process(self) -> bool:
        """Return whether the caller owns the interaction and must process it."""
        return self.status is InteractionClaimStatus.CLAIMED


class InteractionOutcomeStatus(StrEnum):
    """Terminal state recorded for a claimed interaction."""

    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StoredInteractionResponse:
    """Previously published response replayed for a duplicate interaction."""

    idempotency_key: str
    conversation_id: ConversationId
    status: InteractionOutcomeStatus
    text: str | None
    completed_at: datetime

    def __post_init__(self) -> None:
        if _OPAQUE_KEY.fullmatch(self.idempotency_key) is None:
            raise ValueError("invalid idempotency key")
        if self.text is not None and len(self.text) > _MAX_STORED_RESPONSE_CHARS:
            raise ValueError("stored response is too long")
        _require_aware(self.completed_at)
