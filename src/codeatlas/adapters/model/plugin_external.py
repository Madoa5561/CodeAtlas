"""Lifecycle wrapper for optional external OpenAI/Anthropic/Gemini providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from codeatlas import API_VERSION
from codeatlas.domain.agent_models import ModelRequest, ModelResponse
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin

logger = logging.getLogger(__name__)

_DEGRADED_DISABLED = "provider_disabled"


class ExternalProvider(Protocol):
    """Minimal lifecycle surface shared by external model providers."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    async def check_available(self) -> bool: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class ExternalModelPlugin:
    """Lifecycle owner for an OpenAI, Anthropic, or Gemini provider."""

    provider: ExternalProvider
    manifest: PluginManifest
    started: bool = False
    available: bool = False
    degraded_reason: str | None = None

    @property
    def provider_id(self) -> str:
        return self.provider.provider_id

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    async def start(self) -> None:
        """Probe the provider without failing startup when unreachable."""
        if self.started:
            return
        self.started = True
        if not self.provider.enabled:
            self.available = False
            self.degraded_reason = _DEGRADED_DISABLED
            logger.info(
                "external_model_provider_disabled",
                extra={"provider_id": self.provider.provider_id},
            )
            return
        try:
            self.available = await self.provider.check_available()
        except Exception:
            self.available = False
            logger.warning(
                "external_model_probe_failed",
                extra={"provider_id": self.provider.provider_id},
            )
        if self.available:
            self.degraded_reason = None
            logger.info(
                "external_model_provider_available",
                extra={
                    "provider_id": self.provider.provider_id,
                    "model_id": self.provider.model_id,
                },
            )
        else:
            self.degraded_reason = f"{self.provider.provider_id}_unreachable"
            logger.warning(
                "external_model_provider_unavailable",
                extra={
                    "provider_id": self.provider.provider_id,
                    "degraded_reason": self.degraded_reason,
                },
            )

    async def stop(self) -> None:
        """Close provider resources; safe to call more than once."""
        try:
            await self.provider.aclose()
        finally:
            self.started = False
            self.available = False


def build_external_plugin(provider: ExternalProvider) -> ExternalModelPlugin:
    """Build a manifest-bearing plugin for an external provider."""
    provider_id = provider.provider_id
    manifest = PluginManifest(
        plugin_id=f"codeatlas.model.{provider_id}",
        version="1.0.0",
        api_version_min=API_VERSION,
        api_version_max=API_VERSION,
        capabilities=(PluginCapability("model", provider_id, "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )
    return ExternalModelPlugin(provider=provider, manifest=manifest)
