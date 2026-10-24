"""Discord Forum interaction adapter (M5)."""

from codeatlas.adapters.discord.adapter import (
    DISCORD_MESSAGE_LIMIT,
    DiscordInteractionClient,
    InteractionHandler,
    build_intents,
    canonical_interaction_from_message,
    capabilities_from_settings,
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
from codeatlas.adapters.discord.presentation import (
    MAX_RESPONSE_CHUNKS,
    render_citation_footer,
    render_markdown,
    render_response,
    split_markdown,
)

__all__ = [
    "DISCORD_INTERACTION_CAPABILITY",
    "DISCORD_MESSAGE_LIMIT",
    "MAX_RESPONSE_CHUNKS",
    "DiscordInteractionClient",
    "DiscordInteractionPlugin",
    "InteractionHandler",
    "build_intents",
    "canonical_interaction_from_message",
    "capabilities_from_settings",
    "is_forum_starter",
    "is_forum_thread",
    "is_reply_to_bot",
    "mentions_bot",
    "render_citation_footer",
    "render_markdown",
    "render_response",
    "safe_error_message",
    "should_ignore_author",
    "should_respond",
    "split_markdown",
]
