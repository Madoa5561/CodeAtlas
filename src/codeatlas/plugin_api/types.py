"""Lifecycle contract implemented by trusted CodeAtlas plugins."""

from __future__ import annotations

from typing import Protocol

from codeatlas.plugin_api.manifest import PluginManifest


class PluginLifecycle(Protocol):
    """Minimal asynchronous lifecycle for a trusted plugin instance."""

    @property
    def manifest(self) -> PluginManifest:
        """Return immutable plugin metadata."""
        ...

    async def start(self) -> None:
        """Start the plugin without blocking the event loop."""
        ...

    async def stop(self) -> None:
        """Release resources acquired during start."""
        ...
