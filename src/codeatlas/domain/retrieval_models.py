"""Framework-independent models for indexed retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from codeatlas.domain.indexing_models import (
    IndexGenerationStatus,
    SourceSpan,
    SymbolId,
    SymbolKind,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath

_SHA = re.compile(r"^[0-9a-f]{40}$")


class RelationKind(StrEnum):
    """Persisted relation categories exposed to retrieval tools."""

    IMPORT = "import"
    EXPORT = "export"
    CALL = "call"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class IndexTextHit:
    """One lexical match inside safe indexed file content."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    line: int
    excerpt: str
    start_column: int
    end_column: int

    def __post_init__(self) -> None:
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("index text hit requires a full commit SHA")
        if self.line < 1 or self.start_column < 0 or self.end_column < self.start_column:
            raise ValueError("invalid index text hit")
        if not self.excerpt.strip():
            raise ValueError("index text hit requires an excerpt")


@dataclass(frozen=True, slots=True)
class IndexLexicalHit:
    """One BM25-ranked lexical match from the FTS5 projection."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    line: int
    excerpt: str
    score: float

    def __post_init__(self) -> None:
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("lexical hit requires a full commit SHA")
        if self.line < 1:
            raise ValueError("invalid lexical hit line")
        if not self.excerpt.strip():
            raise ValueError("lexical hit requires an excerpt")
        if self.score < 0.0:
            raise ValueError("lexical hit score cannot be negative")


@dataclass(frozen=True, slots=True)
class IndexedRelation:
    """One parser-derived relation candidate with immutable provenance."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    relation_kind: RelationKind
    raw_target: str
    span: SourceSpan
    source_symbol_id: SymbolId | None = None
    confidence: float = 0.8

    def __post_init__(self) -> None:
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("indexed relation requires a full commit SHA")
        if not self.raw_target.strip():
            raise ValueError("indexed relation target is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid relation confidence")


@dataclass(frozen=True, slots=True)
class SymbolQuery:
    """Bounded symbol lookup against the active index generation."""

    name: str | None = None
    qualified_name: str | None = None
    kinds: tuple[SymbolKind, ...] = ()
    path_prefix: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if self.name is not None and len(self.name) > 512:
            raise ValueError("symbol name is too long")
        if self.qualified_name is not None and len(self.qualified_name) > 1_024:
            raise ValueError("qualified symbol name is too long")
        if self.path_prefix is not None and (
            "\x00" in self.path_prefix
            or "\\" in self.path_prefix
            or self.path_prefix.startswith("/")
        ):
            raise ValueError("invalid symbol path filter")
        if not 1 <= self.limit <= 1_000:
            raise ValueError("invalid symbol result limit")
        if self.name is None and self.qualified_name is None and not self.kinds:
            raise ValueError("a symbol query requires a name, qualified name, or kind")


@dataclass(frozen=True, slots=True)
class IndexSnapshotSummary:
    """Safe metadata about the active generation used for evidence provenance."""

    repository_id: RepositoryId
    commit_sha: str
    generation_id: str
    generation_number: int
    status: IndexGenerationStatus

    def __post_init__(self) -> None:
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("index snapshot requires a full commit SHA")
        if self.generation_number < 1:
            raise ValueError("invalid generation number")


@dataclass(frozen=True, slots=True)
class RetrievalLimits:
    """Upper bounds imposed by the retrieval adapter."""

    max_results: int = 50
    max_excerpt_chars: int = 4_000
    max_content_chars: int = 40_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_results <= 1_000:
            raise ValueError("invalid retrieval result limit")
        if not 80 <= self.max_excerpt_chars <= 20_000:
            raise ValueError("invalid retrieval excerpt limit")
        if not 1_000 <= self.max_content_chars <= 200_000:
            raise ValueError("invalid retrieval content limit")


def stable_evidence_key(*parts: str) -> str:
    """Derive a deterministic opaque evidence identifier from provenance parts."""
    payload = "\x00".join(parts).encode("utf-8")
    return f"ev_{hashlib.sha256(payload).hexdigest()[:24]}"
