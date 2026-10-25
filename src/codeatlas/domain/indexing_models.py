"""Framework-independent models for transactional repository indexing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from codeatlas.domain.repository_models import RepositoryId, RepositoryPath

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z]+_[0-9a-f]{32}$")


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class IndexGenerationId:
    """Opaque identity for one immutable index generation."""

    value: str

    def __post_init__(self) -> None:
        if _ID.fullmatch(self.value) is None or not self.value.startswith("gen_"):
            raise ValueError("invalid generation ID")


class IndexGenerationStatus(StrEnum):
    """Persisted lifecycle states for one generation."""

    PENDING = "pending"
    RUNNING = "running"
    VALIDATING = "validating"
    ACTIVE = "active"
    FAILED = "failed"
    SUPERSEDED = "superseded"


_TRANSITIONS: dict[IndexGenerationStatus, frozenset[IndexGenerationStatus]] = {
    IndexGenerationStatus.PENDING: frozenset(
        {IndexGenerationStatus.RUNNING, IndexGenerationStatus.FAILED}
    ),
    IndexGenerationStatus.RUNNING: frozenset(
        {IndexGenerationStatus.VALIDATING, IndexGenerationStatus.FAILED}
    ),
    IndexGenerationStatus.VALIDATING: frozenset(
        {IndexGenerationStatus.ACTIVE, IndexGenerationStatus.FAILED}
    ),
    IndexGenerationStatus.ACTIVE: frozenset({IndexGenerationStatus.SUPERSEDED}),
    IndexGenerationStatus.SUPERSEDED: frozenset({IndexGenerationStatus.ACTIVE}),
    IndexGenerationStatus.FAILED: frozenset(),
}


def validate_generation_transition(
    current: IndexGenerationStatus, target: IndexGenerationStatus
) -> None:
    """Reject a generation lifecycle transition not allowed by domain policy."""
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid generation transition: {current.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """A repository commit associated with one generation."""

    repository_id: RepositoryId
    commit_sha: str
    generation_id: IndexGenerationId
    generation_number: int
    parser_fingerprint: str
    policy_fingerprint: str
    parser_metadata: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _HEX_40.fullmatch(self.commit_sha) is None or self.generation_number < 1:
            raise ValueError("invalid repository snapshot")


@dataclass(frozen=True, slots=True)
class IndexedFileId:
    """Logical path-based identity for an indexed file."""

    value: str

    @classmethod
    def derive(cls, repository_id: RepositoryId, path: RepositoryPath) -> IndexedFileId:
        """Derive a stable logical identity from repository and normalized path."""
        return cls(_stable_id("file", repository_id.value, path.as_posix()))

    def __post_init__(self) -> None:
        if _ID.fullmatch(self.value) is None or not self.value.startswith("file_"):
            raise ValueError("invalid indexed file ID")


@dataclass(frozen=True, slots=True)
class FileVersion:
    """Immutable content identity for a logical file at a commit."""

    file_id: IndexedFileId
    commit_sha: str
    git_object_id: str
    content_hash: str


class ContentSecurityStatus(StrEnum):
    """Security disposition persisted without unsafe content."""

    SAFE = "safe"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """Validated file metadata and optional safe text for one generation."""

    file_id: IndexedFileId
    generation_id: IndexGenerationId
    path: RepositoryPath
    version: FileVersion
    byte_size: int
    line_count: int
    language: str | None
    encoding: str | None
    security_status: ContentSecurityStatus
    status_reason: str | None
    content: str | None
    parser_id: str | None = None
    parser_version: str | None = None
    grammar_version: str | None = None
    syntax_error_count: int = 0
    reused: bool = False


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open byte and line/column coordinates in one immutable file."""

    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        if (
            min(
                self.start_byte,
                self.end_byte,
                self.start_line,
                self.start_column,
                self.end_line,
                self.end_column,
            )
            < 0
            or self.end_byte < self.start_byte
        ):
            raise ValueError("invalid source span")


@dataclass(frozen=True, slots=True)
class SymbolId:
    """Collision-resistant symbol identity within a repository snapshot."""

    value: str

    @classmethod
    def derive(
        cls,
        repository_id: RepositoryId,
        commit_sha: str,
        language: str,
        path: RepositoryPath,
        kind: SymbolKind,
        qualified_name: str,
        signature: str,
        span: SourceSpan,
    ) -> SymbolId:
        """Derive identity including syntax position to separate overloads and anonymous nodes."""
        return cls(
            _stable_id(
                "symbol",
                repository_id.value,
                commit_sha,
                language,
                path.as_posix(),
                kind.value,
                qualified_name,
                signature,
                str(span.start_byte),
                str(span.end_byte),
            )
        )

    def __post_init__(self) -> None:
        if _ID.fullmatch(self.value) is None or not self.value.startswith("symbol_"):
            raise ValueError("invalid symbol ID")


class SymbolKind(StrEnum):
    """Declaration kinds emitted by built-in parsers."""

    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    VARIABLE = "variable"
    NAMESPACE = "namespace"
    ANONYMOUS_FUNCTION = "anonymous_function"
    SERVICE = "service"
    STRUCT = "struct"
    UNION = "union"
    EXCEPTION = "exception"
    FIELD = "field"
    TYPEDEF = "typedef"
    CONST = "const"


@dataclass(frozen=True, slots=True)
class IndexedSymbol:
    """One parser-derived declaration with immutable provenance."""

    symbol_id: SymbolId
    generation_id: IndexGenerationId
    file_id: IndexedFileId
    kind: SymbolKind
    name: str
    qualified_name: str
    signature: str
    span: SourceSpan
    parent_symbol_id: SymbolId | None
    exported: bool
    has_documentation: bool
    parser_id: str
    parser_version: str
    grammar_version: str
    confidence: float = 1.0
    derivation_kind: str = "verified"


class RelationResolutionStatus(StrEnum):
    """Resolution state for a parser-derived relation candidate."""

    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class RelationBase:
    """Common evidence carried by an unresolved or resolved relation."""

    generation_id: IndexGenerationId
    file_id: IndexedFileId
    source_symbol_id: SymbolId | None
    raw_target: str
    span: SourceSpan
    resolution_status: RelationResolutionStatus = RelationResolutionStatus.UNRESOLVED
    confidence: float = 0.8
    resolver_metadata: str | None = None


@dataclass(frozen=True, slots=True)
class ImportRelation(RelationBase):
    """File import target text."""


@dataclass(frozen=True, slots=True)
class ExportRelation(RelationBase):
    """Exported symbol or target text."""


@dataclass(frozen=True, slots=True)
class ReferenceRelation(RelationBase):
    """Call, extends, implements, or identifier reference candidate."""

    relation_kind: str = "reference"


@dataclass(frozen=True, slots=True)
class IndexingRequest:
    """Request to produce a new immutable generation."""

    repository_id: RepositoryId
    revision: str
    incremental: bool = False


@dataclass(frozen=True, slots=True)
class IndexingStatistics:
    """Deterministic counters for one indexing attempt."""

    discovered: int = 0
    indexed: int = 0
    parsed: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    symbols: int = 0
    relations: int = 0
    skip_reasons: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexingFailure:
    """Safe persisted failure information without source or secret material."""

    code: str
    safe_message: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class IndexingResult:
    """Result of a successfully activated generation."""

    snapshot: RepositorySnapshot
    status: IndexGenerationStatus
    statistics: IndexingStatistics
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Framework-independent parser output consumed by the indexer."""

    symbols: tuple[IndexedSymbol, ...]
    imports: tuple[ImportRelation, ...]
    exports: tuple[ExportRelation, ...]
    references: tuple[ReferenceRelation, ...]
    syntax_error_count: int
    syntax_error_spans: tuple[SourceSpan, ...]
