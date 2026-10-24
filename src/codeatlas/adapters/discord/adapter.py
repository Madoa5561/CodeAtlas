"""Discord Forum inbound adapter built on discord.py.

The adapter only translates Discord events into channel-neutral
:class:`CanonicalInteraction` values, invokes the injected application handler,
and publishes the rendered chunks.  All policy decisions live in
:mod:`codeatlas.adapters.discord.filters` and presentation lives in
:mod:`codeatlas.adapters.discord.presentation`, so the discord.Client glue stays
small and testable without a live connection.
"""

from __future__ import annotations

import logging
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Sequence,
)
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

import discord

from codeatlas.adapters.discord import filters
from codeatlas.adapters.discord.presentation import MAX_RESPONSE_CHUNKS, render_response
from codeatlas.application.interactions import InteractionOutcome
from codeatlas.config.settings import DiscordSettings
from codeatlas.domain.interaction_models import (
    Actor,
    AttachmentRef,
    CanonicalInteraction,
    ConversationRef,
    InboundMessage,
    InteractionCapabilities,
    InteractionKind,
)

logger = logging.getLogger(__name__)

InteractionHandler = Callable[[CanonicalInteraction], Awaitable[InteractionOutcome]]

DISCORD_MESSAGE_LIMIT = 2_000
"""Hard Discord message length; it is below every configured response budget."""

_MAX_INBOUND_CONTENT_CHARS = 20_000
_MAX_DISPLAY_NAME_CHARS = 128
_MAX_FILENAME_CHARS = 255
_MAX_ATTACHMENTS = 8
_MAX_TRACKED_MESSAGE_IDS = 2_048

_SAFE_ERROR_JA = "回答の生成中に問題が発生しました。時間をおいて再試行してください。"
_SAFE_ERROR_EN = "Something went wrong while generating an answer. Please try again later."


def safe_error_message(locale: str) -> str:
    """Return the only text the adapter shows on an internal failure."""
    return _SAFE_ERROR_EN if locale.lower().startswith("en") else _SAFE_ERROR_JA


def build_intents(*, message_content: bool = True) -> discord.Intents:
    """Build the minimal guild intents needed by the Forum adapter.

    ``guilds`` delivers thread lifecycle events and ``guild_messages`` delivers
    thread messages.  ``message_content`` is the privileged intent Discord uses
    to gate message bodies: without it, a Forum starter post and ordinary
    follow-up text arrive empty even though thread metadata is present.  It is
    therefore enabled here; operators must also enable it in the Discord
    Developer Portal.  When the bot is mentioned or replied to, Discord still
    delivers content, so the bot remains partially usable without the intent.
    """
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = message_content
    return intents


def capabilities_from_settings(settings: DiscordSettings) -> InteractionCapabilities:
    """Return the presentation capabilities advertised for a Forum thread."""
    return InteractionCapabilities(
        max_response_chars=min(settings.max_response_chars, DISCORD_MESSAGE_LIMIT),
        supports_markdown=True,
        supports_code_blocks=True,
        supports_typing=True,
        supports_threads=True,
    )


class _MessageLike(Protocol):
    """The subset of discord.Message needed by the pure conversion function."""

    @property
    def id(self) -> int: ...

    @property
    def content(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def author(self) -> object: ...

    @property
    def channel(self) -> object: ...

    @property
    def reference(self) -> object | None: ...

    @property
    def attachments(self) -> Sequence[object]: ...

    @property
    def mentions(self) -> Sequence[object]: ...


def _safe_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _safe_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _aware_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.now(UTC)


def _display_name(author: object, *, author_id: int) -> str:
    for attribute in ("display_name", "global_name", "name"):
        candidate = getattr(author, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:_MAX_DISPLAY_NAME_CHARS]
    return f"user {author_id}"


def _compile_question(title: str | None, body: str) -> str:
    clean_title = " ".join(title.split()) if title else ""
    clean_body = body.strip()
    if clean_title and clean_body:
        question = f"{clean_title}\n\n{clean_body}"
    else:
        question = clean_title or clean_body
    if len(question) > _MAX_INBOUND_CONTENT_CHARS:
        question = question[: _MAX_INBOUND_CONTENT_CHARS - 1] + "…"
    return question


def _attachment_refs(attachments: Iterable[object]) -> tuple[AttachmentRef, ...]:
    references: list[AttachmentRef] = []
    for attachment in attachments:
        if len(references) >= _MAX_ATTACHMENTS:
            break
        attachment_id = _safe_int(getattr(attachment, "id", None))
        filename = _safe_str(getattr(attachment, "filename", None)).strip()
        if attachment_id is None or not filename:
            continue
        content_type_value = getattr(attachment, "content_type", None)
        content_type = (
            content_type_value
            if isinstance(content_type_value, str) and content_type_value.strip()
            else None
        )
        size = _safe_int(getattr(attachment, "size", None)) or 0
        references.append(
            AttachmentRef(
                attachment_id=str(attachment_id),
                filename=filename[:_MAX_FILENAME_CHARS],
                content_type=content_type,
                size_bytes=max(0, size),
            )
        )
    return tuple(references)


def _thread_parent_id(channel: object) -> int | None:
    if not (isinstance(channel, discord.Thread) or getattr(channel, "is_thread", False)):
        return None
    return _safe_int(getattr(channel, "parent_id", None))


def canonical_interaction_from_message(
    message: _MessageLike,
    *,
    kind: InteractionKind,
    settings: DiscordSettings,
    thread_id: int,
    thread_title: str | None = None,
    parent_channel_id: int | None = None,
) -> CanonicalInteraction | None:
    """Build a validated :class:`CanonicalInteraction` from one Discord message.

    Returns ``None`` when the Discord object lacks a required identifier or when
    the normalized value fails domain validation; the caller then ignores the
    event instead of leaking diagnostics into the channel.
    """
    message_id = _safe_int(message.id)
    author = message.author
    author_id = _safe_int(getattr(author, "id", None))
    if message_id is None or author_id is None:
        return None

    channel = message.channel
    resolved_parent = (
        parent_channel_id if parent_channel_id is not None else _thread_parent_id(channel)
    )
    body = _safe_str(message.content)
    content = _compile_question(thread_title, body) if kind is InteractionKind.FORUM_POST else body
    if not content.strip():
        return None
    if len(content) > _MAX_INBOUND_CONTENT_CHARS:
        content = content[: _MAX_INBOUND_CONTENT_CHARS - 1] + "…"

    reference = message.reference
    reference_message_id = (
        _safe_int(getattr(reference, "message_id", None)) if reference is not None else None
    )
    if reference_message_id is not None and not (1 <= reference_message_id <= 10**20):
        reference_message_id = None

    try:
        return CanonicalInteraction(
            interaction_id=f"discord_msg_{message_id}",
            idempotency_key=f"discord:{message_id}",
            kind=kind,
            actor=Actor(
                actor_id=f"discord_user_{author_id}",
                display_name=_display_name(author, author_id=author_id),
                is_bot=bool(getattr(author, "bot", False)),
            ),
            conversation=ConversationRef(
                conversation_id=f"discord_thread_{thread_id}",
                source="discord",
                parent_id=(
                    f"discord_channel_{resolved_parent}" if resolved_parent is not None else None
                ),
                is_thread=True,
            ),
            message=InboundMessage(
                message_id=str(message_id),
                content=content,
                created_at=_aware_utc(message.created_at),
                reply_to_message_id=(
                    str(reference_message_id) if reference_message_id is not None else None
                ),
                attachments=_attachment_refs(message.attachments),
            ),
            capabilities=capabilities_from_settings(settings),
            locale=settings.locale,
        )
    except ValueError:
        return None


class _BoundedIdSet(Collection[int]):
    """Insertion-ordered, bounded set used for duplicate suppression."""

    __slots__ = ("_capacity", "_ids")

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._ids: dict[int, None] = {}

    def __contains__(self, value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value in self._ids

    def __iter__(self) -> Iterator[int]:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, value: int) -> None:
        if value in self._ids:
            return
        if len(self._ids) >= self._capacity:
            self._ids.pop(next(iter(self._ids)))
        self._ids[value] = None


def _log_safe_error(event: str, error: BaseException | None = None) -> None:
    """Log an event without exception text, message content, or credentials."""
    extra = {"error_type": type(error).__name__} if error is not None else None
    logger.warning(event, extra=extra)


@asynccontextmanager
async def _typing_indicator(target: object) -> AsyncIterator[None]:
    """Show typing in the thread while the handler runs; never fail the answer."""
    factory = getattr(target, "typing", None)
    if not callable(factory):
        yield
        return
    try:
        context = factory()
    except Exception as error:  # pragma: no cover - defensive around discord.py
        _log_safe_error("discord_typing_start_failed", error)
        yield
        return
    try:
        await context.__aenter__()
    except Exception as error:
        _log_safe_error("discord_typing_start_failed", error)
        yield
        return
    try:
        yield
    finally:
        try:
            await context.__aexit__(None, None, None)
        except Exception as error:
            _log_safe_error("discord_typing_stop_failed", error)


class DiscordInteractionClient(discord.Client):
    """A discord.Client that answers configured Forum posts and threads."""

    def __init__(
        self,
        *,
        handler: InteractionHandler,
        settings: DiscordSettings,
        intents: discord.Intents | None = None,
        bot_user_id: int | None = None,
        max_response_chunks: int = MAX_RESPONSE_CHUNKS,
    ) -> None:
        super().__init__(intents=intents if intents is not None else build_intents())
        self._handler = handler
        self._settings = settings
        self._explicit_bot_user_id = bot_user_id
        self._max_response_chunks = max_response_chunks
        self._seen_message_ids = _BoundedIdSet(_MAX_TRACKED_MESSAGE_IDS)
        self._bot_message_ids = _BoundedIdSet(_MAX_TRACKED_MESSAGE_IDS)

    @property
    def settings(self) -> DiscordSettings:
        """Return the immutable adapter settings."""
        return self._settings

    @property
    def bot_user_id(self) -> int | None:
        """Return the bot's own user ID once the gateway login completed."""
        if self._explicit_bot_user_id is not None:
            return self._explicit_bot_user_id
        user = self.user
        if user is None:
            return None
        return _safe_int(user.id)

    async def on_ready(self) -> None:
        """Log readiness without guild or message content."""
        logger.info("discord_client_ready", extra={"guild_count": len(self.guilds)})

    async def on_thread_create(self, thread: discord.Thread) -> None:
        """Answer a new Forum post when the thread belongs to the configured forum."""
        try:
            await self.process_forum_thread(thread)
        except Exception as error:
            _log_safe_error("discord_thread_create_failed", error)

    async def on_message(self, message: discord.Message) -> None:
        """Answer a Forum thread message that matches the configured reply policy."""
        try:
            await self.process_message(message)
        except Exception as error:
            _log_safe_error("discord_message_failed", error)

    async def process_forum_thread(self, thread: discord.Thread) -> None:
        """Convert the starter post of a new Forum thread into an interaction."""
        settings = self._settings
        if not settings.answer_new_forum_posts:
            return
        thread_id = _safe_int(getattr(thread, "id", None))
        parent_id = _safe_int(getattr(thread, "parent_id", None))
        if thread_id is None:
            return
        if not filters.is_forum_thread(
            parent_channel_id=parent_id,
            forum_channel_id=settings.forum_channel_id,
        ):
            return
        if thread_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(thread_id)
        starter = thread.starter_message
        if starter is None:
            try:
                starter = await thread.fetch_message(thread_id)
            except discord.NotFound:
                logger.info("discord_starter_message_missing")
                return
            except discord.HTTPException as error:
                _log_safe_error("discord_starter_message_fetch_failed", error)
                return
        await self._process_message(
            starter,
            kind=InteractionKind.FORUM_POST,
            target=thread,
            thread_title=_safe_str(getattr(thread, "name", None)),
        )

    async def process_message(self, message: discord.Message) -> None:
        """Apply bot filtering, forum detection, and the reply policy."""
        settings = self._settings
        author = message.author
        author_id = _safe_int(getattr(author, "id", None))
        if author_id is None:
            return
        if filters.should_ignore_author(
            author_id=author_id,
            author_is_bot=bool(getattr(author, "bot", False)),
            bot_user_id=self.bot_user_id,
            ignore_bots=settings.ignore_bots,
        ):
            return

        message_id = _safe_int(getattr(message, "id", None))
        channel = message.channel
        channel_id = _safe_int(getattr(channel, "id", None))
        if message_id is None or channel_id is None:
            return
        if not filters.is_forum_thread(
            parent_channel_id=_thread_parent_id(channel),
            forum_channel_id=settings.forum_channel_id,
        ):
            return
        if message_id in self._seen_message_ids:
            return
        if filters.is_forum_starter(message_id=message_id, thread_id=channel_id):
            if not settings.answer_new_forum_posts:
                return
            self._seen_message_ids.add(message_id)
            await self._process_message(
                message,
                kind=InteractionKind.FORUM_POST,
                target=channel,
                thread_title=_safe_str(getattr(channel, "name", None)),
            )
            return

        mentioned = filters.mentioned_user_ids_from(mentions=message.mentions)
        reference = message.reference
        reference_message_id = (
            _safe_int(getattr(reference, "message_id", None)) if reference is not None else None
        )
        resolved_author_id = filters.resolved_reference_author_id_from(
            resolved=getattr(reference, "resolved", None)
        )
        is_mention = filters.mentions_bot(
            bot_user_id=self.bot_user_id,
            mentioned_user_ids=mentioned,
            content=_safe_str(message.content),
        )
        is_reply = filters.is_reply_to_bot(
            bot_user_id=self.bot_user_id,
            reference_message_id=reference_message_id,
            resolved_author_id=resolved_author_id,
            known_bot_message_ids=self._bot_message_ids,
        )
        if not filters.should_respond(
            policy=settings.reply_policy,
            is_mention=is_mention,
            is_reply_to_bot=is_reply,
        ):
            return
        self._seen_message_ids.add(message_id)
        await self._process_message(
            message,
            kind=InteractionKind.THREAD_MESSAGE,
            target=channel,
        )

    async def _process_message(
        self,
        message: _MessageLike,
        *,
        kind: InteractionKind,
        target: object,
        thread_title: str | None = None,
    ) -> None:
        thread_id = _safe_int(getattr(target, "id", None))
        if thread_id is None:
            return
        interaction = canonical_interaction_from_message(
            message,
            kind=kind,
            settings=self._settings,
            thread_id=thread_id,
            thread_title=thread_title or _safe_str(getattr(target, "name", None)),
            parent_channel_id=_thread_parent_id(target),
        )
        if interaction is None:
            logger.info("discord_interaction_skipped_invalid")
            return

        outcome = await self._invoke_handler(interaction, target)
        if outcome is None:
            await self._send_safe_error(target)
            return
        if not outcome.processed:
            logger.info(
                "discord_duplicate_interaction_skipped",
                extra={"claim_status": outcome.claim_status.value},
            )
            return
        response = outcome.response
        if response is None:
            await self._send_safe_error(target)
            return
        chunks = render_response(
            response,
            max_chars=min(self._settings.max_response_chars, DISCORD_MESSAGE_LIMIT),
            locale=self._settings.locale,
            max_chunks=self._max_response_chunks,
        )
        if not chunks:
            await self._send_safe_error(target)
            return
        for chunk in chunks:
            sent = await self._publish(target, chunk)
            if sent is None:
                return
            self._remember_bot_message(sent)

    async def _invoke_handler(
        self,
        interaction: CanonicalInteraction,
        target: object,
    ) -> InteractionOutcome | None:
        try:
            async with _typing_indicator(target):
                return await self._handler(interaction)
        except Exception as error:
            _log_safe_error("discord_interaction_handler_failed", error)
            return None

    async def _publish(self, target: object, chunk: str) -> object | None:
        send = getattr(target, "send", None)
        if not callable(send):
            return None
        try:
            result: object = await send(chunk)
            return result
        except discord.HTTPException as error:
            _log_safe_error("discord_publish_failed", error)
            return None
        except Exception as error:  # pragma: no cover - defensive around discord.py
            _log_safe_error("discord_publish_failed", error)
            return None

    async def _send_safe_error(self, target: object) -> None:
        sent = await self._publish(target, safe_error_message(self._settings.locale))
        if sent is not None:
            self._remember_bot_message(sent)

    def _remember_bot_message(self, message: object) -> None:
        message_id = _safe_int(getattr(message, "id", None))
        if message_id is not None:
            self._bot_message_ids.add(message_id)


__all__ = [
    "DISCORD_MESSAGE_LIMIT",
    "DiscordInteractionClient",
    "InteractionHandler",
    "build_intents",
    "canonical_interaction_from_message",
    "capabilities_from_settings",
    "safe_error_message",
]
