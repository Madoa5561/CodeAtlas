"""Plugin registry validation and lifecycle tests."""

from dataclasses import dataclass, field

import pytest

from codeatlas.plugin_api import (
    CapabilityConflictError,
    PluginCapability,
    PluginCompatibilityError,
    PluginManifest,
    PluginNotAllowedError,
    PluginRegistry,
    PluginStartupError,
)


@dataclass
class FakePlugin:
    """Deterministic plugin lifecycle fixture."""

    manifest: PluginManifest
    fail_start: bool = False
    events: list[str] = field(default_factory=list)

    async def start(self) -> None:
        self.events.append("start")
        if self.fail_start:
            raise RuntimeError("start failed")

    async def stop(self) -> None:
        self.events.append("stop")


def make_plugin(
    plugin_id: str,
    *,
    capability_name: str | None = None,
    api_min: str = "0.1.0",
    api_max: str = "0.1.0",
    fail_start: bool = False,
) -> FakePlugin:
    """Create a compatible test plugin with an optional capability."""
    capabilities = ()
    if capability_name is not None:
        capabilities = (PluginCapability("interaction", capability_name, "1.0.0"),)
    return FakePlugin(
        PluginManifest(plugin_id, "1.0.0", api_min, api_max, capabilities),
        fail_start=fail_start,
    )


def test_register_and_resolve_capability() -> None:
    """A registered capability resolves to its owning plugin."""
    registry = PluginRegistry(api_version="0.1.0")
    plugin = make_plugin("example.discord", capability_name="discord")
    registry.register(plugin)
    assert registry.resolve("interaction:discord") is plugin


def test_capability_conflict_is_rejected() -> None:
    """Two plugins cannot claim the same capability implicitly."""
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("example.one", capability_name="discord"))
    with pytest.raises(CapabilityConflictError):
        registry.register(make_plugin("example.two", capability_name="discord"))


def test_incompatible_plugin_is_rejected() -> None:
    """Plugin API ranges are checked at registration time."""
    registry = PluginRegistry(api_version="0.1.0")
    with pytest.raises(PluginCompatibilityError):
        registry.register(make_plugin("example.future", api_min="2.0.0", api_max="3.0.0"))


def test_allowlist_is_enforced() -> None:
    """A non-empty allowlist rejects undeclared plugin IDs."""
    registry = PluginRegistry(api_version="0.1.0", allowlist=("example.allowed",))
    with pytest.raises(PluginNotAllowedError):
        registry.register(make_plugin("example.denied"))


@pytest.mark.asyncio
async def test_lifecycle_starts_and_stops_in_order() -> None:
    """Registered plugins start in order and stop in reverse order."""
    registry = PluginRegistry(api_version="0.1.0")
    first = make_plugin("example.first")
    second = make_plugin("example.second")
    registry.register(first)
    registry.register(second)

    await registry.start_all()
    await registry.stop_all()

    assert first.events == ["start", "stop"]
    assert second.events == ["start", "stop"]


@pytest.mark.asyncio
async def test_start_failure_rolls_back_started_plugins() -> None:
    """A failed start stops every plugin that started successfully."""
    registry = PluginRegistry(api_version="0.1.0")
    first = make_plugin("example.first")
    failing = make_plugin("example.failing", fail_start=True)
    registry.register(first)
    registry.register(failing)

    with pytest.raises(PluginStartupError) as captured:
        await registry.start_all()

    assert first.events == ["start", "stop"]
    assert failing.events == ["start", "stop"]
    assert isinstance(captured.value.internal_cause, RuntimeError)
