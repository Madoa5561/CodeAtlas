"""Stable plugin contracts and registry for trusted CodeAtlas plugins."""

from codeatlas.plugin_api.manifest import PluginCapability, PluginManifest
from codeatlas.plugin_api.registry import (
    CapabilityConflictError,
    PluginCompatibilityError,
    PluginNotAllowedError,
    PluginRegistrationError,
    PluginRegistry,
    PluginStartupError,
)
from codeatlas.plugin_api.types import PluginLifecycle

__all__ = [
    "CapabilityConflictError",
    "PluginCapability",
    "PluginCompatibilityError",
    "PluginLifecycle",
    "PluginManifest",
    "PluginNotAllowedError",
    "PluginRegistrationError",
    "PluginRegistry",
    "PluginStartupError",
]
