"""Validated in-memory registry for explicitly supplied trusted plugins."""

from __future__ import annotations

from collections.abc import Iterable

from codeatlas.domain.errors import CodeAtlasError
from codeatlas.plugin_api.manifest import PluginCapability, parse_version
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


class PluginStartupError(CodeAtlasError):
    """Plugin startup failed and already-started plugins were rolled back."""

    default_safe_message = "A plugin failed to start."
    code = "plugin_startup_error"

    def __init__(
        self,
        *,
        internal_cause: BaseException,
        rollback_errors: tuple[BaseException, ...] = (),
    ) -> None:
        self.rollback_errors = rollback_errors
        super().__init__(internal_cause=internal_cause)


class PluginRegistry:
    """Register, resolve, start, and stop explicitly provided plugins."""

    def __init__(self, *, api_version: str, allowlist: Iterable[str] = ()) -> None:
        self._api_version = parse_version(api_version)
        self._allowlist = frozenset(allowlist)
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
        if self._allowlist and manifest.plugin_id not in self._allowlist:
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
                rollback_errors: list[Exception] = []
                try:
                    await plugin.stop()
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
                rollback_errors.extend(await self._rollback_started())
                raise PluginStartupError(
                    internal_cause=error,
                    rollback_errors=tuple(rollback_errors),
                ) from error
            self._started.append(plugin)

    async def stop_all(self) -> None:
        """Stop started plugins in reverse order and report all failures."""
        errors = await self._rollback_started()
        if errors:
            raise ExceptionGroup("one or more plugins failed to stop", list(errors))

    async def _rollback_started(self) -> tuple[Exception, ...]:
        errors: list[Exception] = []
        while self._started:
            plugin = self._started.pop()
            try:
                await plugin.stop()
            except Exception as error:
                errors.append(error)
        return tuple(errors)
