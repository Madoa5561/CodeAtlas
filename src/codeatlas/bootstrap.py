"""Composition root for the CodeAtlas M1 application foundation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError as PydanticValidationError

from codeatlas import API_VERSION
from codeatlas.application.container import ApplicationContainer
from codeatlas.config.settings import CodeAtlasSettings, load_settings
from codeatlas.domain.errors import ConfigurationError
from codeatlas.infrastructure.logging import configure_logging
from codeatlas.plugin_api.registry import PluginRegistry
from codeatlas.plugin_api.types import PluginLifecycle


def create_application(
    *,
    settings: CodeAtlasSettings | None = None,
    env_file: Path | str | None = None,
    plugins: Iterable[PluginLifecycle] = (),
    log_stream: TextIO | None = None,
) -> ApplicationContainer:
    """Build an application container without starting external connections."""
    try:
        resolved_settings = settings or load_settings(env_file)
    except PydanticValidationError as error:
        raise ConfigurationError(internal_cause=error) from error

    configure_logging(resolved_settings.logging, stream=log_stream)
    registry = PluginRegistry(
        api_version=API_VERSION,
        allowlist=resolved_settings.security.plugin_allowlist,
    )
    for plugin in plugins:
        registry.register(plugin)
    return ApplicationContainer(settings=resolved_settings, plugins=registry)
