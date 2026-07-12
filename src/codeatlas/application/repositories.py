"""Application services for registered read-only repositories."""

from __future__ import annotations

from pathlib import Path

from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryLocation
from codeatlas.domain.repository_ports import RepositoryProviderPort


class RepositoryApplicationService:
    """Channel-independent facade over a repository provider port."""

    def __init__(self, provider: RepositoryProviderPort) -> None:
        self.provider = provider

    async def register_local_repository(self, path: Path) -> RepositoryDescriptor:
        """Resolve and register a local repository without exposing adapter details."""
        return await self.provider.register_local_repository(RepositoryLocation(path.resolve()))
