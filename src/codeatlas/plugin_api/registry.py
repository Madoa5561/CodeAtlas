"""Validated in-memory registry for explicitly supplied trusted plugins."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from codeatlas.domain.errors import CodeAtlasError
from codeatlas.plugin_api.manifest import PluginCapability, PluginOrigin, parse_version
from codeatlas.plugin_api.types import PluginLifecycle


class PluginRegistrationError(CodeAtlasError):
    """A plugin could not be registered safely."""

    default_safe_message = "A plugin could not be registered."
    code = "plugin_registration_error"


class PluginCompatibilityError(PluginRegistrationError):
    """A plugin does not support the active Plugin API version."""

    default_safe_message = "A plugin is incompatible with this CodeAtlas version."
    code = "plugin_compatibility_error"


class CapabilityConflictError(PluginRegistrationError):
    """Multiple plugins claimed the same capability."""

    default_safe_message = "Multiple plugins provide the same capability."
    code = "plugin_capability_conflict"


class PluginNotAllowedError(PluginRegistrationError):
    """A plugin was rejected by the configured allowlist."""

    default_safe_message = "A plugin is not allowed by configuration."
    code = "plugin_not_allowed"


@dataclass(frozen=True, slots=True, repr=False)
class PluginLifecycleFailure:
    """Internal diagnostic for one failed plugin lifecycle operation."""

    plugin_id: str
    phase: str
    error: Exception

    def __repr__(self) -> str:
        return f"PluginLifecycleFailure(plugin_id={self.plugin_id!r}, phase={self.phase!r})"


class PluginStartupError(CodeAtlasError):
    """Plugin startup failed and already-started plugins were rolled back."""

    default_safe_message = "A plugin failed to start."
    code = "plugin_startup_error"

    def __init__(
        self,
        *,
        failures: tuple[PluginLifecycleFailure, ...],
    ) -> None:
        self.failures = failures
        super().__init__(internal_cause=failures[0].error)


class PluginShutdownError(CodeAtlasError):
    """One or more plugins failed while every stop was still attempted."""

    default_safe_message = "One or more plugins failed to stop."
    code = "plugin_shutdown_error"

    def __init__(self, *, failures: tuple[PluginLifecycleFailure, ...]) -> None:
        self.failures = failures
        super().__init__(internal_cause=failures[0].error)


class PluginRegistry:
    """Register, resolve, start, and stop explicitly provided plugins."""

    def __init__(self, *, api_version: str, allowlist: Iterable[str] | None = None) -> None:
        self._api_version = parse_version(api_version)
        self._allowlist = None if allowlist is None else frozenset(allowlist)
        self._plugins: dict[str, PluginLifecycle] = {}
        self._capabilities: dict[str, tuple[PluginCapability, PluginLifecycle]] = {}
        self._started: list[PluginLifecycle] = []

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        """Return registered plugin identifiers in registration order."""
        return tuple(self._plugins)

    def register(self, plugin: PluginLifecycle) -> None:
        """Validate and register one explicitly constructed plugin instance."""
        manifest = plugin.manifest
        allowed_by_default = self._allowlist is None and manifest.origin is PluginOrigin.BUILT_IN
        allowed_by_id = self._allowlist is not None and manifest.plugin_id in self._allowlist
        if not (allowed_by_default or allowed_by_id):
            raise PluginNotAllowedError()
        if manifest.plugin_id in self._plugins:
            raise PluginRegistrationError("A plugin ID is already registered.")
        minimum = parse_version(manifest.api_version_min)
        maximum = parse_version(manifest.api_version_max)
        if not minimum <= self._api_version <= maximum:
            raise PluginCompatibilityError()
        for capability in manifest.capabilities:
            if capability.capability_id in self._capabilities:
                raise CapabilityConflictError()

        self._plugins[manifest.plugin_id] = plugin
        for capability in manifest.capabilities:
            self._capabilities[capability.capability_id] = (capability, plugin)

    def resolve(self, capability_id: str) -> PluginLifecycle:
        """Resolve a registered plugin by its kind:name capability ID."""
        registered = self._capabilities.get(capability_id)
        if registered is None:
            raise PluginRegistrationError("The requested plugin capability is unavailable.")
        return registered[1]

    async def start_all(self) -> None:
        """Start plugins in order and roll back prior starts on failure."""
        if self._started:
            return
        for plugin in self._plugins.values():
            try:
                await plugin.start()
            except Exception as error:
                failures = [
                    PluginLifecycleFailure(plugin.manifest.plugin_id, "start", error),
                    *await self._stop_started(),
                ]
                raise PluginStartupError(failures=tuple(failures)) from error
            self._started.append(plugin)

    async def stop_all(self) -> None:
        """Stop started plugins in reverse order and report all failures."""
        failures = await self._stop_started()
        if failures:
            raise PluginShutdownError(failures=failures)

    async def _stop_started(self) -> tuple[PluginLifecycleFailure, ...]:
        failures: list[PluginLifecycleFailure] = []
        while self._started:
            plugin = self._started.pop()
            try:
                await plugin.stop()
            except Exception as error:
                failures.append(PluginLifecycleFailure(plugin.manifest.plugin_id, "stop", error))
        return tuple(failures)
