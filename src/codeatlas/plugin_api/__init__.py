"""Stable plugin contracts and registry for trusted CodeAtlas plugins."""

from codeatlas.plugin_api.manifest import PluginCapability, PluginManifest, PluginOrigin
from codeatlas.plugin_api.registry import (
    CapabilityConflictError,
    PluginCompatibilityError,
    PluginLifecycleFailure,
    PluginNotAllowedError,
    PluginRegistrationError,
    PluginRegistry,
    PluginShutdownError,
    PluginStartupError,
)
from codeatlas.plugin_api.types import PluginLifecycle

__all__ = [
    "CapabilityConflictError",
    "PluginCapability",
    "PluginCompatibilityError",
    "PluginLifecycle",
    "PluginLifecycleFailure",
    "PluginManifest",
    "PluginNotAllowedError",
    "PluginOrigin",
    "PluginRegistrationError",
    "PluginRegistry",
    "PluginShutdownError",
    "PluginStartupError",
]
