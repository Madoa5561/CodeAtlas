"""Immutable metadata for CodeAtlas plugins and capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse the supported numeric subset of semantic versioning."""
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid semantic version: {value!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


@dataclass(frozen=True, slots=True)
class PluginCapability:
    """A uniquely resolvable capability exposed by a plugin."""

    kind: str
    name: str
    version: str

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.kind) is None:
            raise ValueError("capability kind must be a lowercase identifier")
        if _IDENTIFIER.fullmatch(self.name) is None:
            raise ValueError("capability name must be a lowercase identifier")
        parse_version(self.version)

    @property
    def capability_id(self) -> str:
        """Return the stable kind:name identifier used for conflict detection."""
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Plugin identity, API compatibility range, and declared capabilities."""

    plugin_id: str
    version: str
    api_version_min: str
    api_version_max: str
    capabilities: tuple[PluginCapability, ...] = ()

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.plugin_id) is None:
            raise ValueError("plugin_id must be a lowercase identifier")
        parse_version(self.version)
        minimum = parse_version(self.api_version_min)
        maximum = parse_version(self.api_version_max)
        if minimum > maximum:
            raise ValueError("api_version_min must not exceed api_version_max")
        capability_ids = [capability.capability_id for capability in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("a plugin cannot declare a capability more than once")
