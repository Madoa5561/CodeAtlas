"""Minimal application container lifecycle for the M1 foundation."""

from __future__ import annotations

from dataclasses import dataclass

from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.plugin_api.registry import PluginRegistry


@dataclass(slots=True)
class ApplicationContainer:
    """Own foundation services and coordinate their lifecycle."""

    settings: CodeAtlasSettings
    plugins: PluginRegistry
    _started: bool = False

    @property
    def started(self) -> bool:
        """Return whether the application lifecycle is active."""
        return self._started

    async def start(self) -> None:
        """Start foundation services without connecting to external systems."""
        if self._started:
            return
        await self.plugins.start_all()
        self._started = True

    async def stop(self) -> None:
        """Stop foundation services in reverse dependency order."""
        if not self._started:
            return
        try:
            await self.plugins.stop_all()
        finally:
            self._started = False

    async def __aenter__(self) -> ApplicationContainer:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.stop()
