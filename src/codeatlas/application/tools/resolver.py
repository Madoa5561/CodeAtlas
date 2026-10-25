"""Shared resolution, authorization, and payload helpers for retrieval tools."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol

from codeatlas.domain.agent_models import ToolContext
from codeatlas.domain.errors import ForbiddenError, InputValidationError, NotFoundError
from codeatlas.domain.github_ports import GitHubReadOnlyPort
from codeatlas.domain.indexing_models import (
    IndexedFileId,
    IndexedSymbol,
    SourceSpan,
    SymbolId,
    SymbolKind,
)
from codeatlas.domain.repository_models import (
    CommitInfo,
    DiffResult,
    RepositoryDescriptor,
    RepositoryId,
    RepositoryPath,
    ResolvedRevision,
    RevisionRef,
)
from codeatlas.domain.repository_ports import RepositoryProviderPort, RepositoryRegistryPort
from codeatlas.domain.retrieval_models import (
    IndexedRelation,
    IndexSnapshotSummary,
    RelationKind,
    RetrievalLimits,
    SymbolQuery,
)
from codeatlas.domain.retrieval_ports import IndexQueryPort


class ToolIndexPort(IndexQueryPort, Protocol):
    """Index query surface used by M4 tools.

    The two extra read-only methods are additive adapter capabilities beyond the
    frozen ``IndexQueryPort`` contract; ``SqliteIndexQuery`` implements them and
    they never mutate state.
    """

    async def list_file_paths(
        self,
        generation_id: str,
        *,
        file_ids: Sequence[IndexedFileId] | None = None,
    ) -> dict[IndexedFileId, RepositoryPath]:
        """Map logical file IDs to normalized paths without loading content."""
        ...

    async def find_relations_filtered(
        self,
        generation_id: str,
        *,
        relation_kinds: tuple[RelationKind, ...] | None = None,
        raw_target: str | None = None,
        source_symbol_id: SymbolId | None = None,
        path: RepositoryPath | None = None,
        limit: int = 100,
    ) -> tuple[IndexedRelation, ...]:
        """Return relations with every optional filter combined in one bounded query."""
        ...


@dataclass(frozen=True, slots=True)
class ToolServices:
    """Ports and limits available to every tool handler."""

    repository: RepositoryProviderPort
    registry: RepositoryRegistryPort
    index: ToolIndexPort
    limits: RetrievalLimits = field(default_factory=RetrievalLimits)
    github: GitHubReadOnlyPort | None = None
    default_github_repository: str | None = None


def authorized_repositories(
    services: ToolServices, context: ToolContext
) -> tuple[RepositoryDescriptor, ...]:
    """Return registered repositories permitted by the request scope.

    An empty scope means "no explicit restriction"; the in-memory registry remains
    the hard allowlist. A non-empty scope restricts results to those repository IDs.
    """
    repositories = services.registry.list_all()
    if not context.allowed_repository_ids:
        return repositories
    allowed = set(context.allowed_repository_ids)
    return tuple(item for item in repositories if item.repository_id in allowed)


def resolve_repository(
    services: ToolServices, context: ToolContext, raw_repository_id: str | None
) -> RepositoryDescriptor:
    """Resolve the requested repository or the single authorized default."""
    if raw_repository_id is not None:
        try:
            repository_id = RepositoryId(raw_repository_id)
        except ValueError as error:
            raise InputValidationError(
                "The repository identifier is invalid.", internal_cause=error
            ) from error
        authorized = authorized_repositories(services, context)
        if any(item.repository_id == repository_id for item in authorized):
            return services.registry.get(repository_id)
        registered = {item.repository_id for item in services.registry.list_all()}
        if repository_id not in registered:
            raise NotFoundError("The repository is not registered.")
        raise ForbiddenError("The repository is not authorized for this request.")
    authorized = authorized_repositories(services, context)
    if len(authorized) == 1:
        return authorized[0]
    if not authorized:
        if not services.registry.list_all():
            raise NotFoundError("No repository is registered.")
        raise ForbiddenError("No repository is authorized for this request.")
    raise InputValidationError("Multiple repositories are available; specify repository_id.")


async def resolve_snapshot(
    services: ToolServices, descriptor: RepositoryDescriptor
) -> IndexSnapshotSummary | None:
    """Return the active generation summary, or None when nothing is active."""
    return await services.index.get_active_snapshot(descriptor.repository_id)


async def require_snapshot(
    services: ToolServices, descriptor: RepositoryDescriptor
) -> tuple[IndexSnapshotSummary, str]:
    """Require an active generation and return its summary and raw generation ID."""
    snapshot = await resolve_snapshot(services, descriptor)
    if snapshot is None:
        raise NotFoundError("No active code index is available for the repository.")
    return snapshot, snapshot.generation_id


async def resolve_revision(
    services: ToolServices,
    context: ToolContext,
    descriptor: RepositoryDescriptor,
    raw_revision: str | None,
) -> ResolvedRevision:
    """Resolve an explicit revision, else the active index SHA, else HEAD."""
    if raw_revision is not None:
        try:
            reference = RevisionRef(raw_revision)
        except ValueError as error:
            raise InputValidationError(
                "The requested revision is invalid.", internal_cause=error
            ) from error
        return await services.repository.resolve_revision(descriptor.repository_id, reference)
    snapshot = await resolve_snapshot(services, descriptor)
    commit_sha = snapshot.commit_sha if snapshot is not None else context.default_commit_sha
    if commit_sha is None:
        return await services.repository.resolve_revision(
            descriptor.repository_id, RevisionRef("HEAD")
        )
    return ResolvedRevision(descriptor.repository_id, commit_sha, RevisionRef(commit_sha))


def parse_path(raw: str | None, *, allow_root: bool) -> RepositoryPath:
    """Validate and normalize a model-provided repository-relative POSIX path."""
    if raw is None or raw == "":
        if allow_root:
            return RepositoryPath.root()
        raise InputValidationError("A repository file path is required.")
    try:
        path = RepositoryPath(PurePosixPath(raw))
    except ValueError as error:
        raise InputValidationError(
            "The repository path is invalid.", internal_cause=error
        ) from error
    if path.is_root and not allow_root:
        raise InputValidationError("A repository file path is required.")
    return path


def clamp_limit(requested: int, maximum: int) -> int:
    """Clamp a schema-validated limit to the adapter maximum."""
    return max(1, min(requested, maximum))


def kind_filter(names: Sequence[str]) -> tuple[SymbolKind, ...]:
    """Convert schema-validated kind names to domain symbol kinds."""
    return tuple(SymbolKind(name) for name in names)


def build_symbol_query(
    *,
    name: str | None,
    qualified_name: str | None,
    kinds: Sequence[str],
    path_prefix: str | None,
    limit: int,
) -> SymbolQuery:
    """Build a bounded domain symbol query from validated tool arguments."""
    try:
        return SymbolQuery(
            name=name,
            qualified_name=qualified_name,
            kinds=kind_filter(kinds),
            path_prefix=path_prefix,
            limit=limit,
        )
    except ValueError as error:
        raise InputValidationError("The symbol query is invalid.", internal_cause=error) from error


def iso_timestamp(value: datetime) -> str:
    """Render a timezone-aware datetime deterministically as UTC ISO-8601."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_timestamp(raw: str, field_name: str) -> datetime:
    """Parse an ISO-8601 bound and default a missing zone to UTC."""
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        raise InputValidationError(
            f"The commit search {field_name} timestamp is invalid.", internal_cause=error
        ) from error
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def bounded_text(value: str, maximum: int) -> tuple[str, bool]:
    """Truncate text to the given character maximum."""
    if len(value) <= maximum:
        return value, False
    return value[:maximum], True


def repository_payload(descriptor: RepositoryDescriptor) -> dict[str, object]:
    """Return safe repository metadata without any filesystem path."""
    return {
        "repository_id": descriptor.repository_id.value,
        "name": descriptor.name,
        "default_branch": descriptor.default_branch,
        "registered_at": iso_timestamp(descriptor.registered_at),
    }


def symbol_payload(symbol: IndexedSymbol, path: RepositoryPath) -> dict[str, object]:
    """Return a JSON-safe symbol record with commit-independent provenance."""
    return {
        "symbol_id": symbol.symbol_id.value,
        "kind": symbol.kind.value,
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "signature": symbol.signature,
        "path": path.as_posix(),
        "start_line": symbol.span.start_line,
        "end_line": symbol.span.end_line,
        "start_column": symbol.span.start_column,
        "end_column": symbol.span.end_column,
        "parent_symbol_id": (
            symbol.parent_symbol_id.value if symbol.parent_symbol_id is not None else None
        ),
        "exported": symbol.exported,
        "has_documentation": symbol.has_documentation,
        "confidence": symbol.confidence,
        "derivation_kind": symbol.derivation_kind,
    }


def relation_payload(relation: IndexedRelation) -> dict[str, object]:
    """Return a JSON-safe relation record with immutable provenance."""
    return {
        "path": relation.path.as_posix(),
        "relation_kind": relation.relation_kind.value,
        "raw_target": relation.raw_target,
        "start_line": relation.span.start_line,
        "end_line": relation.span.end_line,
        "start_column": relation.span.start_column,
        "end_column": relation.span.end_column,
        "source_symbol_id": (
            relation.source_symbol_id.value if relation.source_symbol_id is not None else None
        ),
        "confidence": relation.confidence,
    }


def commit_payload(commit: CommitInfo, *, max_text_chars: int) -> dict[str, object]:
    """Return JSON-safe commit metadata with a bounded body."""
    body, body_truncated = bounded_text(commit.body, max_text_chars)
    return {
        "commit_sha": commit.commit_sha,
        "parent_shas": list(commit.parent_shas),
        "author_name": commit.author_name,
        "author_email": commit.author_email,
        "authored_at": iso_timestamp(commit.authored_at),
        "committed_at": iso_timestamp(commit.committed_at),
        "subject": commit.subject,
        "body": body,
        "body_truncated": body_truncated,
    }


def diff_payload(diff: DiffResult, *, max_text_chars: int) -> tuple[dict[str, object], bool]:
    """Return a bounded JSON-safe diff payload and its truncation flag."""
    text, truncated = bounded_text(diff.text, max_text_chars)
    return (
        {
            "base_sha": diff.base_sha,
            "head_sha": diff.head_sha,
            "byte_size": diff.byte_size,
            "warnings": list(diff.warnings),
            "text": text,
        },
        truncated or diff.truncated,
    )


def span_bounds(span: SourceSpan) -> tuple[int, int]:
    """Return positive, ordered 1-based evidence line bounds for one span."""
    start = max(1, span.start_line)
    return start, max(start, span.end_line)
