"""Read-only local Git repository adapter components."""

from codeatlas.adapters.repository.local_git import LocalGitRepositoryProvider
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.adapters.repository.registry import InMemoryRepositoryRegistry

__all__ = [
    "InMemoryRepositoryRegistry",
    "LocalGitRepositoryPlugin",
    "LocalGitRepositoryProvider",
]
