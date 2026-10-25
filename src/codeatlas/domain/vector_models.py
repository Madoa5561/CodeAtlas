"""Framework-independent vector retrieval models."""

from __future__ import annotations

import re
from dataclasses import dataclass

from codeatlas.domain.repository_models import RepositoryId, RepositoryPath

_SHA = re.compile(r"^[0-9a-f]{40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """One embedded code chunk persisted in a vector collection."""

    point_id: str
    vector: tuple[float, ...]
    repository_id: RepositoryId
    generation_id: str
    commit_sha: str
    path: RepositoryPath
    start_line: int
    end_line: int
    excerpt: str

    def __post_init__(self) -> None:
        if _UUID.fullmatch(self.point_id) is None:
            raise ValueError("vector point ID must be a UUID")
        if not self.vector or len(self.vector) > 4_096:
            raise ValueError("invalid vector")
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("vector point requires a full commit SHA")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid vector line range")
        if not self.excerpt.strip() or len(self.excerpt) > 8_000:
            raise ValueError("invalid vector excerpt")


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    """One cosine-ranked chunk returned by vector search."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    start_line: int
    end_line: int
    excerpt: str
    score: float

    def __post_init__(self) -> None:
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("vector hit requires a full commit SHA")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid vector hit line range")
        if not self.excerpt.strip():
            raise ValueError("vector hit requires an excerpt")
        if not -1.01 <= self.score <= 1.01:
            raise ValueError("invalid vector similarity")
