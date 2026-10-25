"""Embedding provider factory."""

from __future__ import annotations

from codeatlas.adapters.embedding.local import LocalHashEmbedding
from codeatlas.adapters.embedding.openai_compatible import OpenAICompatibleEmbedding
from codeatlas.config.settings import EmbeddingSettings
from codeatlas.domain.vector_ports import EmbeddingPort


def build_embedding_provider(settings: EmbeddingSettings) -> EmbeddingPort:
    """Build the configured embedding provider."""
    if settings.provider == "local":
        return LocalHashEmbedding(settings)
    return OpenAICompatibleEmbedding(settings)


__all__ = ["LocalHashEmbedding", "OpenAICompatibleEmbedding", "build_embedding_provider"]
