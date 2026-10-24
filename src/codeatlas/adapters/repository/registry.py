"""In-memory repository registry used before persistent storage is introduced."""

from __future__ import annotations

from codeatlas.domain.errors import NotFoundError
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryId


class InMemoryRepositoryRegistry:
    """Deduplicate canonical repositories and store descriptors for this process."""

    def __init__(self) -> None:
        self._by_id: dict[RepositoryId, RepositoryDescriptor] = {}
        self._by_canonical_key: dict[str, RepositoryId] = {}

    def add(self, descriptor: RepositoryDescriptor, canonical_key: str) -> RepositoryDescriptor:
        """Add a descriptor or return the existing canonical registration."""
        existing_id = self._by_canonical_key.get(canonical_key)
        if existing_id is not None:
            return self._by_id[existing_id]
        existing = self._by_id.get(descriptor.repository_id)
        if existing is not None:
            self._by_canonical_key[canonical_key] = descriptor.repository_id
            return existing
        self._by_id[descriptor.repository_id] = descriptor
        self._by_canonical_key[canonical_key] = descriptor.repository_id
        return descriptor

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        """Return a descriptor without exposing any registered filesystem path on failure."""
        try:
            return self._by_id[repository_id]
        except KeyError as error:
            raise NotFoundError(
                "The repository is not registered.", internal_cause=error
            ) from error

    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        """Return every descriptor sorted by opaque repository ID for stable catalogs."""
        return tuple(self._by_id[key] for key in sorted(self._by_id, key=lambda item: item.value))
