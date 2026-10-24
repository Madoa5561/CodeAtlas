"""Built-in interaction plugin that owns the Discord Forum client lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from codeatlas.adapters.discord.adapter import (
    DiscordInteractionClient,
    InteractionHandler,
    build_intents,
)
from codeatlas.config.settings import DiscordSettings
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin

logger = logging.getLogger(__name__)

DISCORD_INTERACTION_CAPABILITY = "interaction:discord"
"""Capability ID resolved by the composition root for the Discord interaction."""

_CLIENT_TASK_NAME = "codeatlas-discord"


class DiscordClientPort(Protocol):
    """The discord.Client surface the plugin lifecycle depends on."""

    async def start(self, token: str, *, reconnect: bool = True) -> None: ...

    async def close(self) -> None: ...


ClientFactory = Callable[[InteractionHandler, DiscordSettings], DiscordClientPort]


def _default_client_factory(
    handler: InteractionHandler,
    settings: DiscordSettings,
) -> DiscordClientPort:
    return DiscordInteractionClient(
        handler=handler,
        settings=settings,
        intents=build_intents(),
    )


def _log_client_failure(event: str, error: BaseException) -> None:
    """Log a client lifecycle failure without token or message content."""
    logger.warning(event, extra={"error_type": type(error).__name__})


@dataclass(slots=True)
class DiscordInteractionPlugin:
    """Lifecycle owner for the Discord Forum interaction adapter."""

    handler: InteractionHandler
    settings: DiscordSettings
    client_factory: ClientFactory | None = None
    started: bool = False
    _client: DiscordClientPort | None = field(default=None, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    manifest = PluginManifest(
        plugin_id="codeatlas.interaction.discord",
        version="1.0.0",
        api_version_min="0.1.0",
        api_version_max="0.1.0",
        capabilities=(PluginCapability("interaction", "discord", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    @classmethod
    def from_settings(
        cls,
        handler: InteractionHandler,
        settings: DiscordSettings,
    ) -> DiscordInteractionPlugin:
        """Create the plugin from validated Discord settings and a handler."""
        return cls(handler=handler, settings=settings)

    @property
    def client(self) -> DiscordClientPort | None:
        """Return the running client, if the plugin started one."""
        return self._client

    async def start(self) -> None:
        """Start the gateway client task; disabled or misconfigured is a no-op."""
        if self.started:
            return
        settings = self.settings
        if not settings.enabled:
            logger.info("discord_plugin_disabled")
            return
        if settings.token is None or settings.forum_channel_id is None:
            logger.warning("discord_plugin_misconfigured")
            return
        if self._task is not None:
            return
        client = (self.client_factory or _default_client_factory)(self.handler, settings)
        self._client = client
        self._task = asyncio.create_task(
            self._run_client(client, settings.token.get_secret_value()),
            name=_CLIENT_TASK_NAME,
        )
        self.started = True
        logger.info("discord_plugin_started")

    async def _run_client(self, client: DiscordClientPort, token: str) -> None:
        try:
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log_client_failure("discord_client_failed", error)

    async def stop(self) -> None:
        """Close the gateway session and cancel the background task; idempotent."""
        if not self.started and self._client is None and self._task is None:
            return
        self.started = False
        client, task = self._client, self._task
        self._client = None
        self._task = None
        if client is not None:
            try:
                await client.close()
            except Exception as error:
                _log_client_failure("discord_client_close_failed", error)
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:  # pragma: no cover - defensive
                _log_client_failure("discord_client_task_failed", error)
        logger.info("discord_plugin_stopped")


__all__ = [
    "DISCORD_INTERACTION_CAPABILITY",
    "ClientFactory",
    "DiscordClientPort",
    "DiscordInteractionPlugin",
]
