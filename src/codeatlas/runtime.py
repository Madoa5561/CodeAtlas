"""Composition root for the M4/M5 runtime.

Adapters are constructed here and nowhere else. Domain and application modules
never import this module; it is only used by the service and diagnostic CLIs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import httpx

from codeatlas import __version__
from codeatlas.adapters.discord import DiscordInteractionPlugin
from codeatlas.adapters.http.api import ApiServices, create_api_app
from codeatlas.adapters.http.plugin import ApiServerPlugin
from codeatlas.adapters.model import DeepSeekModelPlugin, OllamaModelPlugin
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.application.agent import AgentOrchestrator
from codeatlas.application.container import ApplicationContainer
from codeatlas.application.interactions import InteractionApplicationService
from codeatlas.application.tools import (
    ToolExecutionPolicy,
    ToolExecutor,
    build_tool_executor,
)
from codeatlas.bootstrap import create_application
from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.domain.agent_models import AgentLimits
from codeatlas.domain.errors import IndexSchemaError
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryLocation
from codeatlas.domain.retrieval_models import RetrievalLimits
from codeatlas.infrastructure.conversation import SqliteConversationStore
from codeatlas.infrastructure.indexing.migrations import current_revision
from codeatlas.infrastructure.indexing.query import SqliteIndexQuery
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION
from codeatlas.infrastructure.webhook.store import SqliteWebhookStore

logger = logging.getLogger(__name__)


def build_agent_limits(settings: CodeAtlasSettings) -> AgentLimits:
    """Translate validated settings into immutable agent budgets."""
    agent = settings.agent
    return AgentLimits(
        max_iterations=agent.max_iterations,
        max_tool_calls=agent.max_tool_calls,
        deadline_seconds=agent.deadline_seconds,
        max_evidence=agent.max_evidence,
        max_plan_steps=agent.max_plan_steps,
        max_response_chars=agent.max_response_chars,
        temperature=agent.temperature,
        max_output_tokens=agent.max_output_tokens,
        model_timeout_seconds=agent.model_timeout_seconds,
    )


def _index_schema_current(database_url: str) -> bool:
    return current_revision(database_url) == SCHEMA_REVISION


@dataclass(slots=True)
class CodeAtlasRuntime:
    """Own the M4/M5 object graph and its process lifecycle."""

    settings: CodeAtlasSettings
    repository_plugin: LocalGitRepositoryPlugin
    model_plugin: OllamaModelPlugin | DeepSeekModelPlugin
    index_query: SqliteIndexQuery
    conversation_store: SqliteConversationStore
    webhook_store: SqliteWebhookStore
    tool_executor: ToolExecutor
    agent: AgentOrchestrator
    interactions: InteractionApplicationService
    container: ApplicationContainer
    discord_plugin: DiscordInteractionPlugin | None = None
    api_plugin: ApiServerPlugin | None = None
    registered_repositories: tuple[RepositoryDescriptor, ...] = ()
    _started: bool = field(default=False, repr=False)

    @classmethod
    def create(
        cls,
        settings: CodeAtlasSettings,
        *,
        enable_discord: bool = True,
        enable_api: bool = True,
        model_transport: httpx.AsyncBaseTransport | None = None,
        discord_client_factory: object | None = None,
        log_stream: TextIO | None = None,
    ) -> CodeAtlasRuntime:
        """Build the runtime without starting external connections.

        ``enable_discord=False`` keeps the diagnostic CLI independent from the
        configured Discord bot. Optional injection points exist for tests only.
        """
        repository_plugin = LocalGitRepositoryPlugin.from_settings(settings.repository)
        index_query = SqliteIndexQuery(
            settings.database.url,
            limits=RetrievalLimits(
                max_results=min(settings.repository.max_search_results, 100),
            ),
        )
        conversation_store = SqliteConversationStore(
            settings.database.url,
            busy_timeout_seconds=settings.database.busy_timeout_seconds,
            claim_lease_seconds=settings.conversation.thread_lock_lease_seconds,
        )
        webhook_store = SqliteWebhookStore(
            settings.database.url,
            busy_timeout_seconds=settings.database.busy_timeout_seconds,
        )
        model_plugin: OllamaModelPlugin | DeepSeekModelPlugin
        if settings.deepseek.enabled:
            model_plugin = DeepSeekModelPlugin.from_settings(
                settings.deepseek,
                transport=model_transport,
            )
        else:
            model_plugin = OllamaModelPlugin.from_settings(
                settings.ollama,
                transport=model_transport,
            )
        tool_executor = build_tool_executor(
            repository=repository_plugin.provider,
            registry=repository_plugin.registry,
            index=index_query,
            limits=RetrievalLimits(
                max_results=min(settings.repository.max_search_results, 100),
            ),
            policy=ToolExecutionPolicy(
                timeout_seconds=min(
                    max(settings.repository.command_timeout_seconds + 10.0, 15.0),
                    120.0,
                ),
                max_payload_chars=60_000,
                max_attempts=1,
                max_evidence=settings.agent.max_evidence,
            ),
        )
        agent = AgentOrchestrator.default(
            executor=tool_executor,
            provider=model_plugin.provider,
            fallback_without_model=settings.agent.fallback_without_model,
        )
        interactions = InteractionApplicationService(
            store=conversation_store,
            agent=agent,
            limits=build_agent_limits(settings),
            history_limit=settings.conversation.history_limit,
            lock_wait_seconds=settings.conversation.thread_lock_wait_seconds,
            lock_lease_seconds=settings.conversation.thread_lock_lease_seconds,
        )
        plugins: list[object] = [repository_plugin, model_plugin]
        discord_plugin: DiscordInteractionPlugin | None = None
        if enable_discord and settings.discord.enabled:
            discord_plugin = DiscordInteractionPlugin.from_settings(
                interactions.handle,
                settings.discord,
            )
            if discord_client_factory is not None:
                discord_plugin.client_factory = discord_client_factory  # type: ignore[assignment]
            plugins.append(discord_plugin)
        api_plugin: ApiServerPlugin | None = None
        if enable_api and settings.api.enabled:
            github_secret = (
                settings.github.webhook_secret.get_secret_value()
                if settings.github.webhook_secret is not None
                else None
            )
            api_app = create_api_app(
                ApiServices(
                    version=__version__,
                    registry=repository_plugin.registry,
                    interactions=interactions,
                    webhook_store=webhook_store,
                    github_webhook_secret=github_secret,
                    allow_ask=settings.api.allow_ask,
                    max_body_bytes=settings.api.max_body_bytes,
                )
            )
            api_plugin = ApiServerPlugin.from_settings(api_app, settings.api)
            plugins.append(api_plugin)
        container = create_application(
            settings=settings,
            plugins=plugins,  # type: ignore[arg-type]
            log_stream=log_stream,
        )
        return cls(
            settings=settings,
            repository_plugin=repository_plugin,
            model_plugin=model_plugin,
            index_query=index_query,
            conversation_store=conversation_store,
            webhook_store=webhook_store,
            tool_executor=tool_executor,
            agent=agent,
            interactions=interactions,
            container=container,
            discord_plugin=discord_plugin,
            api_plugin=api_plugin,
        )

    @property
    def started(self) -> bool:
        """Return whether the runtime lifecycle is active."""
        return self._started

    async def start(self) -> None:
        """Validate schemas, register configured repositories, and start plugins."""
        if self._started:
            return
        schema_current = await asyncio.to_thread(_index_schema_current, self.settings.database.url)
        if not schema_current:
            raise IndexSchemaError()
        await self.conversation_store.assert_schema_current()
        await self.webhook_store.assert_schema_current()
        self.registered_repositories = await self._register_repositories()
        await self.container.start()
        self._started = True
        logger.info(
            "runtime_started",
            extra={
                "repositories": len(self.registered_repositories),
                "model_provider": self.model_plugin.provider.provider_id,
                "model_enabled": self.model_plugin.provider.enabled,
                "model_available": self.model_plugin.available,
                "discord_enabled": self.settings.discord.enabled,
                "api_enabled": self.settings.api.enabled,
            },
        )

    async def stop(self) -> None:
        """Stop plugins and release adapter resources within the deadline."""
        if not self._started:
            return
        try:
            await self.container.stop()
        finally:
            await self.index_query.aclose()
            await self.conversation_store.close()
            await self.webhook_store.close()
            self._started = False
            logger.info("runtime_stopped")

    async def _register_repositories(self) -> tuple[RepositoryDescriptor, ...]:
        descriptors: list[RepositoryDescriptor] = []
        for configured_path in self.settings.repository.registered_paths:
            path = Path(configured_path).expanduser()
            descriptor = await self.repository_plugin.provider.register_local_repository(
                RepositoryLocation(path)
            )
            descriptors.append(descriptor)
            logger.info(
                "repository_registered",
                extra={"repository_id": descriptor.repository_id.value},
            )
        return tuple(descriptors)

    async def __aenter__(self) -> CodeAtlasRuntime:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.stop()
