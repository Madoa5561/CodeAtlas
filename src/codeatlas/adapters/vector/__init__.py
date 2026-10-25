"""Vector index adapter factory."""

from __future__ import annotations

from codeatlas.adapters.vector.qdrant import QdrantVectorIndex
from codeatlas.config.settings import QdrantSettings
from codeatlas.domain.vector_ports import VectorIndexPort


def build_vector_index(settings: QdrantSettings, *, dimensions: int) -> VectorIndexPort:
    """Build the configured vector collection adapter."""
    return QdrantVectorIndex(settings, dimensions=dimensions)


__all__ = ["QdrantVectorIndex", "build_vector_index"]
