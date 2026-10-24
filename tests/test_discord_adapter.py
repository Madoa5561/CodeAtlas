"""Unit tests for the Discord Forum adapter, filters, and plugin lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import discord
import pytest
from pydantic import SecretStr

from codeatlas import API_VERSION
from codeatlas.adapters.discord.adapter import (
    DiscordInteractionClient,
    build_intents,
    canonical_interaction_from_message,
    safe_error_message,
)
from codeatlas.adapters.discord.filters import (
    is_forum_starter,
    is_forum_thread,
    is_reply_to_bot,
    mentions_bot,
    should_ignore_author,
    should_respond,
)
from codeatlas.adapters.discord.plugin import (
    DISCORD_INTERACTION_CAPABILITY,
    DiscordInteractionPlugin,
)
from codeatlas.application.interactions import InteractionOutcome
from codeatlas.config.settings import DiscordSettings, ReplyPolicy
from codeatlas.domain.conversation_models import ConversationId, InteractionClaimStatus
from codeatlas.domain.interaction_models import (
    CanonicalInteraction,
    CanonicalResponse,
    InteractionKind,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)
from codeatlas.plugin_api.manifest import PluginOrigin
from codeatlas.plugin_api.registry import PluginRegistry

FORUM_CHANNEL_ID = 4_242
BOT_USER_ID = 999
OTHER_BOT_ID = 555
THREAD_ID = 10_000
_SENT_TOKEN = "super-secret-discord-token"
_WARNING_LEVEL = logging.WARNING


def _settings(**overrides: object) -> DiscordSettings:
    values: dict[str, object] = {
        "enabled": True,
        "token": SecretStr(_SENT_TOKEN),
        "forum_channel_id": FORUM_CHANNEL_ID,
        "reply_policy": ReplyPolicy.MENTION_OR_REPLY,
        "locale": "ja",
        "max_response_chars": 1_900,
    }
    values.update(overrides)
    return DiscordSettings.model_validate(values)


def _response(text: str = "回答です。") -> CanonicalResponse:
    return CanonicalResponse(
        status=ResponseStatus.OK,
        blocks=(ResponseBlock(kind=ResponseBlockKind.TEXT, text=text),),
        citations=(),
        confidence=0.7,
    )


def _outcome(
    *,
    response: CanonicalResponse | None = None,
    processed: bool = True,
    duplicate: bool = False,
    claim_status: InteractionClaimStatus = InteractionClaimStatus.CLAIMED,
) -> InteractionOutcome:
    return InteractionOutcome(
        processed=processed,
        duplicate=duplicate,
        conversation_id=ConversationId.derive("discord", "test-conversation"),
        claim_status=claim_status,
        response=response if response is not None else _response(),
    )


class RecordingHandler:
    """Async interaction handler that records calls and can fail on demand."""

    def __init__(
        self,
        outcome_factory: Callable[[CanonicalInteraction], InteractionOutcome] | None = None,
    ) -> None:
        self.interactions: list[CanonicalInteraction] = []
        self.error: BaseException | None = None
        self._outcome_factory = outcome_factory

    async def __call__(self, interaction: CanonicalInteraction) -> InteractionOutcome:
        self.interactions.append(interaction)
        if self.error is not None:
            raise self.error
        if self._outcome_factory is not None:
            return self._outcome_factory(interaction)
        return _outcome()


@dataclass
class FakeUser:
    id: int
    name: str = "user"
    display_name: str = "User"
    global_name: str | None = None
    bot: bool = False


@dataclass
class FakeAttachment:
    id: int
    filename: str
    content_type: str | None = "text/plain"
    size: int = 12


@dataclass
class FakeSentMessage:
    id: int
    content: str


@dataclass
class FakeReference:
    message_id: int
    resolved: object | None = None


class FakeTyping:
    def __init__(self, owner: FakeChannel) -> None:
        self._owner = owner

    async def __aenter__(self) -> FakeTyping:
        self._owner.typing_entered += 1
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeChannel:
    def __init__(
        self,
        *,
        channel_id: int,
        parent_id: int | None,
        name: str = "スレッド",
        is_thread: bool = True,
    ) -> None:
        self.id = channel_id
        self.parent_id = parent_id
        self.name = name
        self.is_thread = is_thread
        self.sent: list[FakeSentMessage] = []
        self.typing_entered = 0
        self.send_error: BaseException | None = None
        self._next_id = 0

    def typing(self) -> FakeTyping:
        return FakeTyping(self)

    async def send(self, content: str) -> FakeSentMessage:
        if self.send_error is not None:
            raise self.send_error
        self._next_id += 1
        sent = FakeSentMessage(id=self._next_id, content=content)
        self.sent.append(sent)
        return sent


class FakeThread(FakeChannel):
    def __init__(
        self,
        *,
        channel_id: int = THREAD_ID,
        parent_id: int | None = FORUM_CHANNEL_ID,
        name: str = "新規投稿タイトル",
        starter_message: FakeMessage | None = None,
    ) -> None:
        super().__init__(
            channel_id=channel_id,
            parent_id=parent_id,
            name=name,
            is_thread=True,
        )
        self.starter_message = starter_message
        self.fetch_calls = 0
        self.fetch_result: FakeMessage | None = None

    async def fetch_message(self, message_id: int) -> FakeMessage:
        self.fetch_calls += 1
        if self.fetch_result is None:
            raise AssertionError(f"unexpected fetch for {message_id}")
        return self.fetch_result


@dataclass
class FakeMessage:
    id: int
    author: FakeUser
    channel: FakeChannel
    content: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reference: FakeReference | None = None
    attachments: list[FakeAttachment] = field(default_factory=list)
    mentions: list[FakeUser] = field(default_factory=list)


def _forum_message(
    thread: FakeThread,
    *,
    message_id: int | None = None,
    author: FakeUser | None = None,
    content: str = "本文です。",
    **kwargs: object,
) -> FakeMessage:
    return FakeMessage(
        id=message_id if message_id is not None else thread.id,
        author=author or FakeUser(1, "alice", "Alice"),
        channel=thread,
        content=content,
        **kwargs,
    )


def _client(
    handler: RecordingHandler,
    settings: DiscordSettings | None = None,
) -> DiscordInteractionClient:
    return DiscordInteractionClient(
        handler=handler,
        settings=settings or _settings(),
        bot_user_id=BOT_USER_ID,
    )


async def test_forum_post_is_converted_and_answered() -> None:
    handler = RecordingHandler()
    thread = FakeThread(starter_message=None)
    thread.starter_message = _forum_message(thread, content="本文の質問です。")
    client = _client(handler)

    await client.process_forum_thread(thread)

    assert len(handler.interactions) == 1
    interaction = handler.interactions[0]
    assert interaction.kind is InteractionKind.FORUM_POST
    assert interaction.interaction_id == f"discord_msg_{thread.id}"
    assert interaction.idempotency_key == f"discord:{thread.id}"
    assert interaction.actor.actor_id == "discord_user_1"
    assert interaction.actor.display_name == "Alice"
    assert interaction.conversation.conversation_id == f"discord_thread_{thread.id}"
    assert interaction.conversation.parent_id == f"discord_channel_{FORUM_CHANNEL_ID}"
    assert interaction.conversation.is_thread is True
    assert interaction.message.message_id == str(thread.id)
    assert interaction.message.content == "新規投稿タイトル\n\n本文の質問です。"
    assert interaction.capabilities.max_response_chars == 1_900
    assert interaction.locale == "ja"
    assert thread.typing_entered >= 1
    assert len(thread.sent) == 1
    assert thread.sent[0].content.startswith("回答です。")
    assert "**確信度**" in thread.sent[0].content


async def test_starter_message_is_fetched_when_missing_from_cache() -> None:
    handler = RecordingHandler()
    thread = FakeThread(starter_message=None)
    thread.fetch_result = _forum_message(thread, content="取得した本文")
    client = _client(handler)

    await client.process_forum_thread(thread)

    assert thread.fetch_calls == 1
    assert len(handler.interactions) == 1
    assert handler.interactions[0].message.content == "新規投稿タイトル\n\n取得した本文"


async def test_forum_post_ignored_when_automatic_answers_disabled() -> None:
    handler = RecordingHandler()
    thread = FakeThread(starter_message=_forum_message(FakeThread()))
    client = _client(handler, _settings(answer_new_forum_posts=False))

    await client.process_forum_thread(thread)

    assert handler.interactions == []
    assert thread.sent == []


async def test_thread_from_other_forum_is_ignored() -> None:
    handler = RecordingHandler()
    foreign = FakeThread(parent_id=777, starter_message=None)
    foreign.starter_message = _forum_message(foreign)
    client = _client(handler)

    await client.process_forum_thread(foreign)

    assert handler.interactions == []
    assert foreign.sent == []


async def test_mention_message_in_forum_thread_is_answered() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_001,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> これは何ですか",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(message)

    assert len(handler.interactions) == 1
    interaction = handler.interactions[0]
    assert interaction.kind is InteractionKind.THREAD_MESSAGE
    assert interaction.conversation.conversation_id == f"discord_thread_{THREAD_ID}"
    assert interaction.message.content == "<@999> これは何ですか"
    assert thread.typing_entered >= 1
    assert len(thread.sent) == 1


async def test_plain_message_without_mention_or_reply_is_ignored() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_002, author=FakeUser(2, "bob", "Bob"), channel=thread, content="雑談"
    )
    client = _client(handler)

    await client.process_message(message)

    assert handler.interactions == []
    assert thread.sent == []


async def test_resolved_reply_to_bot_is_answered_under_reply_policy() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    replied = FakeMessage(id=19_000, author=FakeUser(BOT_USER_ID, "codeatlas"), channel=thread)
    message = FakeMessage(
        id=20_003,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="追加の質問です",
        reference=FakeReference(message_id=19_000, resolved=replied),
    )
    client = _client(handler, _settings(reply_policy=ReplyPolicy.REPLY))

    await client.process_message(message)

    assert len(handler.interactions) == 1


async def test_self_and_other_bot_messages_are_ignored() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    self_message = FakeMessage(
        id=20_010,
        author=FakeUser(BOT_USER_ID, "codeatlas", bot=True),
        channel=thread,
        content="<@999>",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    other_bot = FakeMessage(
        id=20_011,
        author=FakeUser(OTHER_BOT_ID, "other-bot", bot=True),
        channel=thread,
        content="<@999>",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(self_message)
    await client.process_message(other_bot)

    assert handler.interactions == []
    assert thread.sent == []


async def test_other_bot_is_answered_when_ignore_bots_is_disabled() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    other_bot = FakeMessage(
        id=20_012,
        author=FakeUser(OTHER_BOT_ID, "other-bot", bot=True),
        channel=thread,
        content="<@999>",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler, _settings(ignore_bots=False))

    await client.process_message(other_bot)

    assert len(handler.interactions) == 1


async def test_message_outside_forum_thread_is_ignored() -> None:
    handler = RecordingHandler()
    channel = FakeChannel(channel_id=77_777, parent_id=777, name="general")
    message = FakeMessage(
        id=20_020,
        author=FakeUser(2, "bob", "Bob"),
        channel=channel,
        content="<@999> hello",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(message)

    assert handler.interactions == []
    assert channel.sent == []


async def test_duplicate_message_is_processed_once() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_030,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> 同じ質問",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(message)
    await client.process_message(message)

    assert len(handler.interactions) == 1
    assert len(thread.sent) == 1


async def test_duplicate_outcome_is_not_published() -> None:
    handler = RecordingHandler(
        lambda interaction: _outcome(
            processed=False,
            duplicate=True,
            claim_status=InteractionClaimStatus.ALREADY_COMPLETED,
        )
    )
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_031,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> 重複",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(message)

    assert len(handler.interactions) == 1
    assert thread.sent == []


async def test_handler_failure_publishes_only_safe_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = RecordingHandler()
    handler.error = RuntimeError(f"internal failure {_SENT_TOKEN}")
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_040,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> 質問本文の秘密",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    with caplog.at_level(_WARNING_LEVEL):
        await client.process_message(message)

    assert [sent.content for sent in thread.sent] == [safe_error_message("ja")]
    assert all(_SENT_TOKEN not in sent.content for sent in thread.sent)
    assert all("質問本文の秘密" not in sent.content for sent in thread.sent)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _SENT_TOKEN not in logged
    assert "質問本文の秘密" not in logged
    assert "internal failure" not in logged


async def test_handler_failure_uses_english_safe_error_when_configured() -> None:
    handler = RecordingHandler()
    handler.error = RuntimeError("boom")
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_041,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> question",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler, _settings(locale="en"))

    await client.process_message(message)

    assert [sent.content for sent in thread.sent] == [safe_error_message("en")]


async def test_publish_http_error_is_caught() -> None:
    handler = RecordingHandler()
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    thread.send_error = discord.HTTPException(
        SimpleNamespace(status=500, reason="internal"),
        "publish failed",
    )
    message = FakeMessage(
        id=20_050,
        author=FakeUser(2, "bob", "Bob"),
        channel=thread,
        content="<@999> 質問",
        mentions=[FakeUser(BOT_USER_ID, "codeatlas")],
    )
    client = _client(handler)

    await client.process_message(message)

    assert len(handler.interactions) == 1
    assert thread.sent == []


def test_canonical_conversion_keeps_bounded_metadata() -> None:
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    attachment = FakeAttachment(
        id=7,
        filename="diagram.png",
        content_type="image/png",
        size=4096,
    )
    message = FakeMessage(
        id=20_060,
        author=FakeUser(3, "carol", "Carol"),
        channel=thread,
        content="本文",
        reference=FakeReference(message_id=321),
        attachments=[attachment],
    )

    interaction = canonical_interaction_from_message(
        message,
        kind=InteractionKind.FORUM_POST,
        settings=_settings(),
        thread_id=THREAD_ID,
        thread_title="タイトル",
    )

    assert interaction is not None
    assert interaction.message.reply_to_message_id == "321"
    assert interaction.message.created_at.tzinfo is not None
    assert interaction.message.created_at.utcoffset() is not None
    assert len(interaction.message.attachments) == 1
    metadata = interaction.message.attachments[0]
    assert metadata.attachment_id == "7"
    assert metadata.filename == "diagram.png"
    assert metadata.content_type == "image/png"
    assert metadata.size_bytes == 4096
    assert interaction.conversation.parent_id == f"discord_channel_{FORUM_CHANNEL_ID}"


def test_canonical_conversion_rejects_empty_question() -> None:
    thread = FakeChannel(channel_id=THREAD_ID, parent_id=FORUM_CHANNEL_ID)
    message = FakeMessage(
        id=20_061,
        author=FakeUser(3, "carol", "Carol"),
        channel=thread,
        content="   ",
    )

    interaction = canonical_interaction_from_message(
        message,
        kind=InteractionKind.THREAD_MESSAGE,
        settings=_settings(),
        thread_id=THREAD_ID,
    )

    assert interaction is None


def test_build_intents_is_minimal() -> None:
    intents = build_intents()

    assert intents.guilds is True
    assert intents.guild_messages is True
    assert intents.message_content is True
    assert intents.members is False
    assert intents.presences is False
    assert intents.reactions is False
    assert build_intents(message_content=False).message_content is False


def test_safe_error_message_locales() -> None:
    assert safe_error_message("ja").startswith("回答")
    assert "Something went wrong" in safe_error_message("en")
    for message in (safe_error_message("ja"), safe_error_message("en")):
        assert "token" not in message.lower()


@pytest.mark.parametrize(
    ("policy", "is_mention", "is_reply", "expected"),
    [
        (ReplyPolicy.ALL, False, False, True),
        (ReplyPolicy.ALL, True, True, True),
        (ReplyPolicy.MENTION, True, False, True),
        (ReplyPolicy.MENTION, False, True, False),
        (ReplyPolicy.MENTION, False, False, False),
        (ReplyPolicy.REPLY, False, True, True),
        (ReplyPolicy.REPLY, True, False, False),
        (ReplyPolicy.REPLY, False, False, False),
        (ReplyPolicy.MENTION_OR_REPLY, True, False, True),
        (ReplyPolicy.MENTION_OR_REPLY, False, True, True),
        (ReplyPolicy.MENTION_OR_REPLY, True, True, True),
        (ReplyPolicy.MENTION_OR_REPLY, False, False, False),
    ],
)
def test_reply_policy_matrix(
    policy: ReplyPolicy,
    is_mention: bool,
    is_reply: bool,
    expected: bool,
) -> None:
    assert (
        should_respond(
            policy=policy,
            is_mention=is_mention,
            is_reply_to_bot=is_reply,
        )
        is expected
    )


def test_bot_filtering_rules() -> None:
    assert should_ignore_author(
        author_id=BOT_USER_ID,
        author_is_bot=True,
        bot_user_id=BOT_USER_ID,
        ignore_bots=False,
    )
    assert should_ignore_author(
        author_id=OTHER_BOT_ID,
        author_is_bot=True,
        bot_user_id=BOT_USER_ID,
        ignore_bots=True,
    )
    assert not should_ignore_author(
        author_id=OTHER_BOT_ID,
        author_is_bot=True,
        bot_user_id=BOT_USER_ID,
        ignore_bots=False,
    )
    assert not should_ignore_author(
        author_id=42,
        author_is_bot=False,
        bot_user_id=BOT_USER_ID,
        ignore_bots=True,
    )


def test_mention_detection_uses_metadata_and_content() -> None:
    assert mentions_bot(bot_user_id=BOT_USER_ID, mentioned_user_ids=[BOT_USER_ID])
    assert mentions_bot(bot_user_id=BOT_USER_ID, content="<@999> hello")
    assert mentions_bot(bot_user_id=BOT_USER_ID, content="<@!999> hello")
    assert not mentions_bot(bot_user_id=BOT_USER_ID, content="hello")
    assert not mentions_bot(bot_user_id=None, content="<@999> hello")


def test_reply_detection_uses_reference_and_known_messages() -> None:
    assert is_reply_to_bot(
        bot_user_id=BOT_USER_ID,
        reference_message_id=77,
        resolved_author_id=BOT_USER_ID,
    )
    assert is_reply_to_bot(
        bot_user_id=BOT_USER_ID,
        reference_message_id=77,
        known_bot_message_ids={77},
    )
    assert not is_reply_to_bot(
        bot_user_id=BOT_USER_ID,
        reference_message_id=77,
        resolved_author_id=42,
    )
    assert not is_reply_to_bot(bot_user_id=BOT_USER_ID, reference_message_id=None)


def test_forum_thread_detection() -> None:
    assert is_forum_thread(
        parent_channel_id=FORUM_CHANNEL_ID,
        forum_channel_id=FORUM_CHANNEL_ID,
    )
    assert not is_forum_thread(
        parent_channel_id=777,
        forum_channel_id=FORUM_CHANNEL_ID,
    )
    assert not is_forum_thread(
        parent_channel_id=None,
        forum_channel_id=FORUM_CHANNEL_ID,
    )
    assert not is_forum_thread(
        parent_channel_id=FORUM_CHANNEL_ID,
        forum_channel_id=None,
    )
    assert not is_forum_thread(
        parent_channel_id=FORUM_CHANNEL_ID,
        forum_channel_id=FORUM_CHANNEL_ID,
        is_thread=False,
    )
    assert is_forum_starter(message_id=THREAD_ID, thread_id=THREAD_ID)
    assert not is_forum_starter(message_id=THREAD_ID + 1, thread_id=THREAD_ID)


class FakeDiscordClient:
    """Stand-in for discord.Client used to exercise the plugin lifecycle."""

    def __init__(self) -> None:
        self.token: str | None = None
        self.closed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        del reconnect
        self.token = token
        self.started.set()
        await self.release.wait()

    async def close(self) -> None:
        self.closed = True
        self.release.set()


async def test_plugin_is_disabled_without_settings() -> None:
    handler = RecordingHandler()
    plugin = DiscordInteractionPlugin.from_settings(
        handler,
        _settings(enabled=False, token=None, forum_channel_id=None),
    )

    await plugin.start()

    assert plugin.started is False
    assert plugin.client is None
    await plugin.stop()
    await plugin.stop()


async def test_plugin_starts_and_stops_the_client() -> None:
    handler = RecordingHandler()
    plugin = DiscordInteractionPlugin.from_settings(handler, _settings())
    fake = FakeDiscordClient()
    plugin.client_factory = lambda handler, settings: fake

    await plugin.start()
    await asyncio.wait_for(fake.started.wait(), timeout=1.0)

    assert plugin.started is True
    assert plugin.client is fake
    assert fake.token == _SENT_TOKEN

    await plugin.stop()

    assert plugin.started is False
    assert plugin.client is None
    assert fake.closed is True
    await plugin.stop()


async def test_plugin_start_is_idempotent() -> None:
    handler = RecordingHandler()
    plugin = DiscordInteractionPlugin.from_settings(handler, _settings())
    fake = FakeDiscordClient()
    plugin.client_factory = lambda handler, settings: fake

    await plugin.start()
    await plugin.start()
    await asyncio.wait_for(fake.started.wait(), timeout=1.0)

    assert fake.token == _SENT_TOKEN

    await plugin.stop()


def test_plugin_manifest_declares_interaction_capability() -> None:
    handler = RecordingHandler()
    plugin = DiscordInteractionPlugin.from_settings(
        handler,
        _settings(enabled=False, token=None, forum_channel_id=None),
    )

    manifest = plugin.manifest

    assert manifest.plugin_id == "codeatlas.interaction.discord"
    assert manifest.origin is PluginOrigin.BUILT_IN
    assert manifest.capabilities[0].capability_id == DISCORD_INTERACTION_CAPABILITY
    assert DISCORD_INTERACTION_CAPABILITY == "interaction:discord"


async def test_plugin_registers_with_the_plugin_registry() -> None:
    handler = RecordingHandler()
    plugin = DiscordInteractionPlugin.from_settings(
        handler,
        _settings(enabled=False, token=None, forum_channel_id=None),
    )
    registry = PluginRegistry(api_version=API_VERSION)

    registry.register(plugin)

    assert registry.plugin_ids == ("codeatlas.interaction.discord",)
    assert registry.resolve(DISCORD_INTERACTION_CAPABILITY) is plugin

    await registry.start_all()
    await registry.stop_all()
