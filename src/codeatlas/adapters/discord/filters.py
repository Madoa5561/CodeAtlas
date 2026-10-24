"""Pure inbound filters for the Discord Forum interaction adapter.

The functions in this module never touch discord.py objects or the network.  The
adapter reads the handful of scalar values it needs from a Discord object and
delegates the policy decision here, which keeps the reply policy, forum thread
detection, and bot filtering independently testable.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

from codeatlas.config.settings import ReplyPolicy

_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


def mentions_bot(
    *,
    bot_user_id: int | None,
    mentioned_user_ids: Iterable[int] = (),
    content: str = "",
) -> bool:
    """Return whether a message pings the bot via the mention list or raw content.

    Discord always supplies the ``mentions`` metadata for a message, but message
    content is gated by the Message Content intent.  Checking both keeps reply
    detection working even when ``content`` is empty.
    """
    if bot_user_id is None:
        return False
    if bot_user_id in tuple(mentioned_user_ids):
        return True
    return any(int(match.group(1)) == bot_user_id for match in _MENTION_PATTERN.finditer(content))


def is_reply_to_bot(
    *,
    bot_user_id: int | None,
    reference_message_id: int | None,
    resolved_author_id: int | None = None,
    known_bot_message_ids: Collection[int] = (),
) -> bool:
    """Return whether a message replies to the bot or to a previously sent answer.

    ``resolved_author_id`` is used when Discord resolved the replied-to message
    (gateway reference) and ``known_bot_message_ids`` covers messages this
    process published itself.
    """
    if bot_user_id is None or reference_message_id is None:
        return False
    if reference_message_id in known_bot_message_ids:
        return True
    return resolved_author_id is not None and resolved_author_id == bot_user_id


def should_ignore_author(
    *,
    author_id: int,
    author_is_bot: bool,
    bot_user_id: int | None,
    ignore_bots: bool,
) -> bool:
    """Return whether the author must be ignored (always self, bots by config)."""
    if bot_user_id is not None and author_id == bot_user_id:
        return True
    return author_is_bot and ignore_bots


def should_respond(
    *,
    policy: ReplyPolicy,
    is_mention: bool,
    is_reply_to_bot: bool,
) -> bool:
    """Apply the configured reply policy to one ordinary thread message."""
    if policy is ReplyPolicy.ALL:
        return True
    if policy is ReplyPolicy.MENTION:
        return is_mention
    if policy is ReplyPolicy.REPLY:
        return is_reply_to_bot
    return is_mention or is_reply_to_bot


def is_forum_thread(
    *,
    parent_channel_id: int | None,
    forum_channel_id: int | None,
    is_thread: bool = True,
) -> bool:
    """Return whether a Discord thread belongs to the configured Forum channel."""
    if not is_thread or parent_channel_id is None or forum_channel_id is None:
        return False
    return parent_channel_id == forum_channel_id


def is_forum_starter(*, message_id: int, thread_id: int) -> bool:
    """Return whether a message is the starter post of its Forum thread.

    Discord uses the starter message ID as the thread ID for Forum posts.
    """
    return message_id == thread_id


def mentioned_user_ids_from(*, mentions: Iterable[object]) -> tuple[int, ...]:
    """Extract plain user IDs from Discord-like mention objects."""
    identifiers: list[int] = []
    for mention in mentions:
        value = getattr(mention, "id", None)
        if isinstance(value, int) and not isinstance(value, bool):
            identifiers.append(value)
    return tuple(identifiers)


def resolved_reference_author_id_from(*, resolved: object | None) -> int | None:
    """Extract the resolved reply author ID from a Discord-like reference."""
    if resolved is None:
        return None
    author = getattr(resolved, "author", None)
    value = getattr(author, "id", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
