"""Channel-independent interaction and citation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

_OPAQUE_ID = re.compile(r"^[\w:.\-]{1,200}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_CONTENT_CHARS = 20_000
_MAX_BLOCK_CHARS = 10_000
_MAX_CITATIONS = 50


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Actor:
    """The human or bot that produced an inbound interaction."""

    actor_id: str
    display_name: str
    is_bot: bool = False

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.actor_id) is None:
            raise ValueError("invalid actor ID")
        if not self.display_name.strip() or len(self.display_name) > 128:
            raise ValueError("invalid actor display name")


@dataclass(frozen=True, slots=True)
class ConversationRef:
    """The channel-specific conversation scope, such as a Discord thread."""

    conversation_id: str
    source: str
    parent_id: str | None = None
    is_thread: bool = False

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.conversation_id) is None:
            raise ValueError("invalid conversation reference")
        if _OPAQUE_ID.fullmatch(self.source) is None:
            raise ValueError("invalid interaction source")
        if self.parent_id is not None and _OPAQUE_ID.fullmatch(self.parent_id) is None:
            raise ValueError("invalid parent conversation reference")


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Metadata for an inbound attachment; contents are never fetched in M5."""

    attachment_id: str
    filename: str
    content_type: str | None
    size_bytes: int

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.attachment_id) is None:
            raise ValueError("invalid attachment ID")
        if not self.filename.strip() or len(self.filename) > 255:
            raise ValueError("invalid attachment filename")
        if self.size_bytes < 0:
            raise ValueError("invalid attachment size")


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One normalized inbound message treated as untrusted data."""

    message_id: str
    content: str
    created_at: datetime
    reply_to_message_id: str | None = None
    attachments: tuple[AttachmentRef, ...] = ()

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.message_id) is None:
            raise ValueError("invalid message ID")
        if len(self.content) > _MAX_CONTENT_CHARS:
            raise ValueError("inbound message is too long")
        _require_aware(self.created_at)
        if self.reply_to_message_id is not None and (
            _OPAQUE_ID.fullmatch(self.reply_to_message_id) is None
        ):
            raise ValueError("invalid reply message ID")


class InteractionKind(StrEnum):
    """Supported inbound interaction categories."""

    FORUM_POST = "forum_post"
    THREAD_MESSAGE = "thread_message"
    CLI = "cli"


@dataclass(frozen=True, slots=True)
class InteractionCapabilities:
    """Presentation capabilities of one inbound channel."""

    max_response_chars: int
    supports_markdown: bool
    supports_code_blocks: bool
    supports_typing: bool
    supports_threads: bool
    max_citations: int = _MAX_CITATIONS

    def __post_init__(self) -> None:
        if not 200 <= self.max_response_chars <= 10_000:
            raise ValueError("invalid maximum response length")
        if not 1 <= self.max_citations <= _MAX_CITATIONS:
            raise ValueError("invalid maximum citation count")

    @classmethod
    def discord_thread(cls) -> InteractionCapabilities:
        """Return the conservative Discord thread presentation limits."""
        return cls(
            max_response_chars=1_900,
            supports_markdown=True,
            supports_code_blocks=True,
            supports_typing=True,
            supports_threads=True,
        )

    @classmethod
    def plain_cli(cls) -> InteractionCapabilities:
        """Return generous terminal presentation limits for diagnostics."""
        return cls(
            max_response_chars=9_000,
            supports_markdown=True,
            supports_code_blocks=True,
            supports_typing=False,
            supports_threads=False,
        )


@dataclass(frozen=True, slots=True)
class CanonicalInteraction:
    """Channel-independent inbound request with its idempotency key."""

    interaction_id: str
    idempotency_key: str
    kind: InteractionKind
    actor: Actor
    conversation: ConversationRef
    message: InboundMessage
    capabilities: InteractionCapabilities
    locale: str = "ja"
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.interaction_id) is None:
            raise ValueError("invalid interaction ID")
        if _OPAQUE_ID.fullmatch(self.idempotency_key) is None:
            raise ValueError("invalid idempotency key")
        if not self.locale.strip() or len(self.locale) > 16:
            raise ValueError("invalid locale")
        _require_aware(self.received_at)


class ResponseStatus(StrEnum):
    """Outcome classification of one canonical response."""

    OK = "ok"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"
    REJECTED = "rejected"


class ResponseBlockKind(StrEnum):
    """UI-neutral response block categories."""

    TEXT = "text"
    CODE = "code"
    BULLETS = "bullets"


@dataclass(frozen=True, slots=True)
class ResponseBlock:
    """One renderable response block that never contains credentials."""

    kind: ResponseBlockKind
    text: str
    language: str | None = None
    citation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("response block text is required")
        if len(self.text) > _MAX_BLOCK_CHARS:
            raise ValueError("response block is too long")
        if self.kind is ResponseBlockKind.CODE:
            if self.language is not None and not re.fullmatch(
                r"[A-Za-z0-9_+.#-]{1,32}", self.language
            ):
                raise ValueError("invalid code language")
        elif self.language is not None:
            raise ValueError("language is only valid for code blocks")
        if any(_OPAQUE_ID.fullmatch(citation_id) is None for citation_id in self.citation_ids):
            raise ValueError("invalid citation reference")


@dataclass(frozen=True, slots=True)
class Citation:
    """A verifiable source location tied to one immutable commit and line span."""

    citation_id: str
    repository_id: str
    commit_sha: str
    path: str
    start_line: int
    end_line: int
    retrieval_method: str
    symbol_id: str | None = None
    symbol_name: str | None = None

    def __post_init__(self) -> None:
        if _OPAQUE_ID.fullmatch(self.citation_id) is None:
            raise ValueError("invalid citation ID")
        if _OPAQUE_ID.fullmatch(self.repository_id) is None:
            raise ValueError("invalid citation repository")
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("citation commit must be a full SHA")
        if not self.path or "\x00" in self.path or "\\" in self.path:
            raise ValueError("invalid citation path")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid citation line range")
        if not self.retrieval_method.strip():
            raise ValueError("citation retrieval method is required")
        if self.symbol_id is not None and _OPAQUE_ID.fullmatch(self.symbol_id) is None:
            raise ValueError("invalid citation symbol")


@dataclass(frozen=True, slots=True)
class CanonicalResponse:
    """Channel-independent answer with verified citations and disclosed gaps."""

    status: ResponseStatus
    blocks: tuple[ResponseBlock, ...]
    citations: tuple[Citation, ...]
    confidence: float = 0.0
    uncertainties: tuple[str, ...] = ()
    search_methods: tuple[str, ...] = ()
    follow_ups: tuple[str, ...] = ()
    safe_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if not self.blocks:
            raise ValueError("a response requires at least one block")
        if len(self.citations) > _MAX_CITATIONS:
            raise ValueError("too many citations")
        citation_ids = [citation.citation_id for citation in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("duplicate citation IDs")
        known = set(citation_ids)
        for block in self.blocks:
            if any(citation_id not in known for citation_id in block.citation_ids):
                raise ValueError("response block references an unknown citation")
        if self.status is ResponseStatus.ERROR and self.safe_error is None:
            raise ValueError("error responses require a safe message")
        _require_aware(self.created_at)

    @property
    def plain_text(self) -> str:
        """Return a neutral text rendering used for persistence and replay."""
        return "\n\n".join(block.text for block in self.blocks)
