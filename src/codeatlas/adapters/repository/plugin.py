"""Built-in plugin wrapper for the local Git repository capability."""

from __future__ import annotations

from dataclasses import dataclass

from codeatlas.adapters.repository.git_runner import SafeGitRunner
from codeatlas.adapters.repository.local_git import LocalGitRepositoryProvider
from codeatlas.adapters.repository.registry import InMemoryRepositoryRegistry
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.repository_models import RepositoryLimits, RepositoryPolicy
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin

LOCAL_REPOSITORY_CAPABILITY = "repository:local-git"


@dataclass(slots=True)
class LocalGitRepositoryPlugin:
    """Lifecycle owner for the built-in local Git repository provider."""

    provider: LocalGitRepositoryProvider
    started: bool = False
    manifest = PluginManifest(
        plugin_id="codeatlas.repository.local-git",
        version="1.0.0",
        api_version_min="0.1.0",
        api_version_max="0.1.0",
        capabilities=(PluginCapability("repository", "local-git", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    @classmethod
    def from_settings(cls, settings: RepositorySettings) -> LocalGitRepositoryPlugin:
        """Create the provider graph from validated M2 settings."""
        limits = RepositoryLimits(
            max_repository_bytes=settings.max_repository_bytes,
            max_text_file_bytes=settings.max_text_file_bytes,
            max_search_file_bytes=settings.max_search_file_bytes,
            max_directory_entries=settings.max_directory_entries,
            max_search_results=settings.max_search_results,
            max_diff_bytes=settings.max_diff_bytes,
            max_command_output_bytes=settings.max_command_output_bytes,
            command_timeout_seconds=settings.command_timeout_seconds,
        )
        policy = RepositoryPolicy(limits=limits, detect_renames=settings.detect_renames)
        runner = SafeGitRunner(
            executable=settings.git_executable,
            timeout_seconds=settings.command_timeout_seconds,
            output_limit_bytes=settings.max_command_output_bytes,
        )
        provider = LocalGitRepositoryProvider(
            InMemoryRepositoryRegistry(),
            runner,
            policy,
            ripgrep_executable=settings.ripgrep_executable,
        )
        return cls(provider)

    async def start(self) -> None:
        """Mark the in-process provider available without external connections."""
        self.started = True

    async def stop(self) -> None:
        """Mark the provider unavailable; no persistent resources are owned in M2."""
        self.started = False
