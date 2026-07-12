"""Built-in parser plugin registration and lifecycle tests."""

from __future__ import annotations

import pytest

from codeatlas.adapters.parsing.tree_sitter_plugin import (
    PARSER_CAPABILITY,
    TypeScriptJavaScriptParserPlugin,
)
from codeatlas.plugin_api import PluginManifest, PluginOrigin
from codeatlas.plugin_api.registry import (
    CapabilityConflictError,
    PluginNotAllowedError,
    PluginRegistry,
)


class DuplicateParserPlugin:
    """Test fixture claiming the production parser capability under another ID."""

    manifest = PluginManifest(
        plugin_id="codeatlas.parser.duplicate",
        version="1.0.0",
        api_version_min="0.1.0",
        api_version_max="0.1.0",
        capabilities=TypeScriptJavaScriptParserPlugin.manifest.capabilities,
        origin=PluginOrigin.THIRD_PARTY,
    )

    async def start(self) -> None:
        """Start the inert fixture."""

    async def stop(self) -> None:
        """Stop the inert fixture."""


def test_parser_plugin_allowlist_three_states() -> None:
    """Built-in parser follows unset, empty, and explicit allowlist semantics."""
    plugin = TypeScriptJavaScriptParserPlugin()
    default = PluginRegistry(api_version="0.1.0")
    default.register(plugin)
    assert default.resolve(PARSER_CAPABILITY) is plugin
    with pytest.raises(PluginNotAllowedError):
        PluginRegistry(api_version="0.1.0", allowlist=[]).register(
            TypeScriptJavaScriptParserPlugin()
        )
    explicit = PluginRegistry(api_version="0.1.0", allowlist=[plugin.manifest.plugin_id])
    explicit.register(TypeScriptJavaScriptParserPlugin())


def test_parser_capability_conflict_is_rejected() -> None:
    """Two grammars cannot silently claim the same parser capability."""
    allowed = PluginRegistry(
        api_version="0.1.0",
        allowlist=[
            TypeScriptJavaScriptParserPlugin.manifest.plugin_id,
            DuplicateParserPlugin.manifest.plugin_id,
        ],
    )
    allowed.register(TypeScriptJavaScriptParserPlugin())
    with pytest.raises(CapabilityConflictError):
        allowed.register(DuplicateParserPlugin())


@pytest.mark.asyncio
async def test_parser_plugin_lifecycle() -> None:
    """Parser lifecycle is owned by the existing registry."""
    plugin = TypeScriptJavaScriptParserPlugin()
    registry = PluginRegistry(api_version="0.1.0")
    registry.register(plugin)
    await registry.start_all()
    assert plugin.started
    await registry.stop_all()
    assert not plugin.started
