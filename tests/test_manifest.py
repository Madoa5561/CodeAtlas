"""Plugin manifest and version compatibility tests."""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from codeatlas.plugin_api import (
    PluginCapability,
    PluginCompatibilityError,
    PluginManifest,
    PluginOrigin,
    PluginRegistry,
)


@dataclass
class ManifestPlugin:
    """Minimal typed lifecycle fixture for manifest registration."""

    manifest: PluginManifest

    async def start(self) -> None:
        """Start without side effects."""

    async def stop(self) -> None:
        """Stop without side effects."""


def test_manifest_defaults_to_third_party() -> None:
    """A plugin is never implicitly classified as built-in."""
    manifest = PluginManifest("example.plugin", "1.0.0", "0.1.0", "0.2.0")
    assert manifest.origin is PluginOrigin.THIRD_PARTY


@pytest.mark.parametrize("version", ["1", "1.0", "v1.0.0", "1.0.0-beta"])
def test_manifest_rejects_unsupported_version_syntax(version: str) -> None:
    """The documented numeric semantic-version subset is enforced."""
    with pytest.raises(ValueError):
        PluginManifest("example.plugin", version, "0.1.0", "0.2.0")


def test_manifest_rejects_inverted_api_range() -> None:
    """Minimum API versions cannot exceed maximum versions."""
    with pytest.raises(ValueError):
        PluginManifest("example.plugin", "1.0.0", "0.2.0", "0.1.0")


@pytest.mark.parametrize(
    ("factory", "value"),
    [
        (lambda value: PluginManifest(value, "1.0.0", "0.1.0", "0.2.0"), "Invalid"),
        (lambda value: PluginCapability(value, "name", "1.0.0"), "Invalid"),
        (lambda value: PluginCapability("kind", value, "1.0.0"), "Invalid"),
        (lambda value: PluginCapability("kind", "name", value), "1.0"),
    ],
)
def test_identifiers_and_capability_versions_are_validated(
    factory: Callable[[str], object],
    value: str,
) -> None:
    """Manifest and capability identifiers use the stable lowercase grammar."""
    with pytest.raises(ValueError):
        factory(value)


def test_manifest_rejects_duplicate_capabilities() -> None:
    """One manifest cannot claim the same capability twice."""
    capability = PluginCapability("interaction", "discord", "1.0.0")
    with pytest.raises(ValueError):
        PluginManifest(
            "example.plugin",
            "1.0.0",
            "0.1.0",
            "0.2.0",
            (capability, capability),
        )


def test_compatibility_range_includes_boundaries() -> None:
    """An API version equal to either inclusive boundary is accepted."""
    plugin = ManifestPlugin(
        PluginManifest(
            "codeatlas.boundary",
            "1.0.0",
            "0.1.0",
            "0.1.0",
            origin=PluginOrigin.BUILT_IN,
        )
    )
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)
    assert registry.plugin_ids == ("codeatlas.boundary",)


def test_compatibility_rejects_api_above_maximum() -> None:
    """A plugin with an older maximum API version is rejected."""
    registry = PluginRegistry(api_version="1.0.0", allowlist=("example.old",))
    plugin = ManifestPlugin(PluginManifest("example.old", "1.0.0", "0.1.0", "0.9.0"))
    with pytest.raises(PluginCompatibilityError):
        registry.register(plugin)
