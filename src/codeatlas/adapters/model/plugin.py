"""Built-in plugin wrapper for the optional Ollama model capability."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from codeatlas import API_VERSION
from codeatlas.adapters.model.ollama import PROVIDER_ID, OllamaModelProvider
from codeatlas.config.settings import OllamaSettings
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin

logger = logging.getLogger(__name__)

OLLAMA_CAPABILITY = f"model:{PROVIDER_ID}"

_DEGRADED_DISABLED = "provider_disabled"
_DEGRADED_UNREACHABLE = "ollama_unreachable"


@dataclass(slots=True)
class OllamaModelPlugin:
    """Lifecycle owner for the optional Ollama model provider."""

    provider: OllamaModelProvider
    started: bool = False
    available: bool = False
    degraded_reason: str | None = None
    manifest = PluginManifest(
        plugin_id="codeatlas.model.ollama",
        version="1.0.0",
        api_version_min=API_VERSION,
        api_version_max=API_VERSION,
        capabilities=(PluginCapability("model", "ollama", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    @classmethod
    def from_settings(
        cls,
        settings: OllamaSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> OllamaModelPlugin:
        """Build the provider and its lifecycle wrapper from Ollama settings."""
        provider = OllamaModelProvider.from_settings(settings, transport=transport, client=client)
        return cls(provider)

    @property
    def model_id(self) -> str:
        """Return the configured model identifier."""
        return self.provider.model_id

    async def start(self) -> None:
        """Probe the endpoint without failing startup when it is unreachable."""
        if self.started:
            return
        self.started = True
        if not self.provider.enabled:
            self.available = False
            self.degraded_reason = _DEGRADED_DISABLED
            logger.info("ollama_provider_disabled", extra={"provider_id": PROVIDER_ID})
            return
        try:
            self.available = await self.provider.check_available()
        except Exception:
            self.available = False
            logger.warning("ollama_provider_probe_failed", extra={"provider_id": PROVIDER_ID})
        if self.available:
            self.degraded_reason = None
            logger.info(
                "ollama_provider_available",
                extra={"provider_id": PROVIDER_ID, "model_id": self.provider.model_id},
            )
        else:
            self.degraded_reason = _DEGRADED_UNREACHABLE
            logger.warning(
                "ollama_provider_unavailable",
                extra={"provider_id": PROVIDER_ID, "degraded_reason": _DEGRADED_UNREACHABLE},
            )

    async def stop(self) -> None:
        """Close provider resources; safe to call more than once."""
        try:
            await self.provider.aclose()
        finally:
            self.started = False
            self.available = False
