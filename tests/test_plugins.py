"""Plugin registry policy and lifecycle tests."""

from dataclasses import dataclass, field

import pytest

from codeatlas.adapters.repository.plugin import (
    LOCAL_REPOSITORY_CAPABILITY,
    LocalGitRepositoryPlugin,
)
from codeatlas.config.settings import RepositorySettings
from codeatlas.plugin_api import (
    CapabilityConflictError,
    PluginCapability,
    PluginCompatibilityError,
    PluginManifest,
    PluginNotAllowedError,
    PluginOrigin,
    PluginRegistrationError,
    PluginRegistry,
    PluginShutdownError,
    PluginStartupError,
)


@dataclass
class FakePlugin:
    """Deterministic plugin fixture recording events in a shared timeline."""

    manifest: PluginManifest
    timeline: list[str] = field(default_factory=list)
    fail_start: bool = False
    fail_stop: bool = False
    secret: str = "lifecycle-secret"

    async def start(self) -> None:
        self.timeline.append(f"start:{self.manifest.plugin_id}")
        if self.fail_start:
            raise RuntimeError(f"token={self.secret}")

    async def stop(self) -> None:
        self.timeline.append(f"stop:{self.manifest.plugin_id}")
        if self.fail_stop:
            raise RuntimeError(f"token={self.secret}")


def make_plugin(
    plugin_id: str,
    *,
    timeline: list[str] | None = None,
    origin: PluginOrigin = PluginOrigin.BUILT_IN,
    capability_name: str | None = None,
    api_min: str = "0.1.0",
    api_max: str = "0.1.0",
    fail_start: bool = False,
    fail_stop: bool = False,
) -> FakePlugin:
    """Create a test plugin with explicit origin and deterministic behavior."""
    capabilities = ()
    if capability_name is not None:
        capabilities = (PluginCapability("interaction", capability_name, "1.0.0"),)
    return FakePlugin(
        PluginManifest(
            plugin_id,
            "1.0.0",
            api_min,
            api_max,
            capabilities,
            origin,
        ),
        timeline=[] if timeline is None else timeline,
        fail_start=fail_start,
        fail_stop=fail_stop,
    )


def test_unset_allowlist_only_allows_explicit_builtin() -> None:
    """Unset policy allows built-ins and rejects default third-party manifests."""
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.builtin"))
    third_party = make_plugin("example.third-party", origin=PluginOrigin.THIRD_PARTY)
    with pytest.raises(PluginNotAllowedError):
        registry.register(third_party)


def test_empty_allowlist_rejects_every_plugin() -> None:
    """An explicit empty list denies built-in and third-party plugins."""
    registry = PluginRegistry(api_version="0.1.0", allowlist=())
    with pytest.raises(PluginNotAllowedError):
        registry.register(make_plugin("codeatlas.builtin"))


def test_id_allowlist_only_allows_listed_plugin() -> None:
    """An ID allowlist permits exactly the listed IDs regardless of origin."""
    registry = PluginRegistry(api_version="0.1.0", allowlist=("example.allowed",))
    allowed = make_plugin("example.allowed", origin=PluginOrigin.THIRD_PARTY)
    registry.register(allowed)
    with pytest.raises(PluginNotAllowedError):
        registry.register(make_plugin("codeatlas.unlisted"))


def test_register_and_resolve_capability() -> None:
    """A registered capability resolves to its owning plugin."""
    registry = PluginRegistry(api_version="0.1.0")
    plugin = make_plugin("codeatlas.discord", capability_name="discord")
    registry.register(plugin)
    assert registry.resolve("interaction:discord") is plugin


def test_duplicate_plugin_id_is_rejected() -> None:
    """A plugin ID identifies exactly one registered lifecycle instance."""
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.duplicate"))
    with pytest.raises(PluginRegistrationError):
        registry.register(make_plugin("codeatlas.duplicate"))


def test_missing_capability_is_reported_safely() -> None:
    """Resolving an unavailable capability returns a safe registry error."""
    registry = PluginRegistry(api_version="0.1.0")
    with pytest.raises(PluginRegistrationError):
        registry.resolve("interaction:missing")


def test_capability_conflict_is_rejected() -> None:
    """Two allowed plugins cannot claim the same capability."""
    registry = PluginRegistry(
        api_version="0.1.0",
        allowlist=("example.one", "example.two"),
    )
    registry.register(make_plugin("example.one", capability_name="discord"))
    with pytest.raises(CapabilityConflictError):
        registry.register(make_plugin("example.two", capability_name="discord"))


def test_incompatible_plugin_is_rejected() -> None:
    """Plugin API ranges are checked at registration time."""
    registry = PluginRegistry(api_version="0.1.0")
    with pytest.raises(PluginCompatibilityError):
        registry.register(make_plugin("codeatlas.future", api_min="2.0.0", api_max="3.0.0"))


@pytest.mark.asyncio
async def test_start_order_and_reverse_stop_order() -> None:
    """Plugins start in registration order and stop in the exact reverse order."""
    timeline: list[str] = []
    registry = PluginRegistry(api_version="0.1.0")
    for name in ("first", "second", "third"):
        registry.register(make_plugin(f"codeatlas.{name}", timeline=timeline))

    await registry.start_all()
    await registry.stop_all()

    assert timeline == [
        "start:codeatlas.first",
        "start:codeatlas.second",
        "start:codeatlas.third",
        "stop:codeatlas.third",
        "stop:codeatlas.second",
        "stop:codeatlas.first",
    ]


@pytest.mark.asyncio
async def test_start_all_is_idempotent_while_started() -> None:
    """Calling start twice never starts the same registered plugin twice."""
    timeline: list[str] = []
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.once", timeline=timeline))
    await registry.start_all()
    await registry.start_all()
    await registry.stop_all()
    assert timeline == ["start:codeatlas.once", "stop:codeatlas.once"]


@pytest.mark.asyncio
async def test_start_failure_rolls_back_only_started_plugins_in_reverse() -> None:
    """A failed plugin is not stopped; prior successful starts roll back in reverse."""
    timeline: list[str] = []
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.first", timeline=timeline))
    registry.register(make_plugin("codeatlas.second", timeline=timeline))
    registry.register(make_plugin("codeatlas.failing", timeline=timeline, fail_start=True))

    with pytest.raises(PluginStartupError) as captured:
        await registry.start_all()

    assert timeline == [
        "start:codeatlas.first",
        "start:codeatlas.second",
        "start:codeatlas.failing",
        "stop:codeatlas.second",
        "stop:codeatlas.first",
    ]
    assert [failure.phase for failure in captured.value.failures] == ["start"]
    assert "lifecycle-secret" not in str(captured.value)
    assert "lifecycle-secret" not in repr(captured.value)


@pytest.mark.asyncio
async def test_normal_stop_attempts_all_plugins_after_failure() -> None:
    """One stop failure does not prevent every remaining plugin from stopping."""
    timeline: list[str] = []
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.first", timeline=timeline))
    registry.register(make_plugin("codeatlas.failing", timeline=timeline, fail_stop=True))
    registry.register(make_plugin("codeatlas.third", timeline=timeline))
    await registry.start_all()

    with pytest.raises(PluginShutdownError) as captured:
        await registry.stop_all()

    assert timeline[-3:] == [
        "stop:codeatlas.third",
        "stop:codeatlas.failing",
        "stop:codeatlas.first",
    ]
    assert [failure.plugin_id for failure in captured.value.failures] == ["codeatlas.failing"]
    assert "lifecycle-secret" not in str(captured.value)
    assert "lifecycle-secret" not in repr(captured.value)


@pytest.mark.asyncio
async def test_rollback_collects_stop_failures_and_continues() -> None:
    """Rollback retains all internal failures while attempting every prior stop."""
    timeline: list[str] = []
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(make_plugin("codeatlas.first", timeline=timeline, fail_stop=True))
    registry.register(make_plugin("codeatlas.second", timeline=timeline, fail_stop=True))
    registry.register(make_plugin("codeatlas.start-fail", timeline=timeline, fail_start=True))

    with pytest.raises(PluginStartupError) as captured:
        await registry.start_all()

    assert timeline[-2:] == ["stop:codeatlas.second", "stop:codeatlas.first"]
    assert [(failure.plugin_id, failure.phase) for failure in captured.value.failures] == [
        ("codeatlas.start-fail", "start"),
        ("codeatlas.second", "stop"),
        ("codeatlas.first", "stop"),
    ]
    assert all("lifecycle-secret" not in repr(failure) for failure in captured.value.failures)


@pytest.mark.asyncio
async def test_builtin_local_repository_plugin_policy_and_lifecycle() -> None:
    """The local provider is an explicit built-in with one stable capability."""
    plugin = LocalGitRepositoryPlugin.from_settings(RepositorySettings())
    assert plugin.manifest.origin is PluginOrigin.BUILT_IN
    assert plugin.manifest.capabilities[0].capability_id == LOCAL_REPOSITORY_CAPABILITY
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)
    assert registry.resolve(LOCAL_REPOSITORY_CAPABILITY) is plugin
    await registry.start_all()
    assert plugin.started is True
    await registry.stop_all()
    assert plugin.started is False


def test_empty_allowlist_disables_local_repository_plugin() -> None:
    """An explicit deny-all policy also rejects the built-in repository provider."""
    registry = PluginRegistry(api_version="0.1.0", allowlist=())
    plugin = LocalGitRepositoryPlugin.from_settings(RepositorySettings())
    with pytest.raises(PluginNotAllowedError):
        registry.register(plugin)


def test_local_repository_capability_conflict_is_rejected() -> None:
    """No second provider can silently replace the built-in local capability."""
    registry = PluginRegistry(
        api_version="0.1.0",
        allowlist=("codeatlas.repository.local-git", "example.repository"),
    )
    registry.register(LocalGitRepositoryPlugin.from_settings(RepositorySettings()))
    conflicting = make_plugin(
        "example.repository",
        origin=PluginOrigin.THIRD_PARTY,
        capability_name=None,
    )
    conflicting.manifest = PluginManifest(
        "example.repository",
        "1.0.0",
        "0.1.0",
        "0.1.0",
        (PluginCapability("repository", "local-git", "1.0.0"),),
    )
    with pytest.raises(CapabilityConflictError):
        registry.register(conflicting)
