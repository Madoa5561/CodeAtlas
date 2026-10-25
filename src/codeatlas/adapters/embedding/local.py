"""Deterministic offline embedding provider for tests and local demos."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from codeatlas.config.settings import EmbeddingSettings

_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]{2,}")


class LocalHashEmbedding:
    """Hashed bag-of-tokens embedding; deterministic and dependency-free.

    This is not a semantic model. It exists for offline wiring, tests, and
    deterministic demos; production should use an OpenAI-compatible embedding
    endpoint.
    """

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._dimensions = settings.dimensions
        self._model_id = "local-hash-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self._dimensions
        for token in _TOKEN.findall(text.casefold())[:2_000]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + (digest[5] % 7) / 7.0)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return tuple(vector)
        return tuple(value / norm for value in vector)

    async def aclose(self) -> None:
        return None
