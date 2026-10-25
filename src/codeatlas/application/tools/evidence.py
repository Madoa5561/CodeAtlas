"""Verified evidence constructors shared by retrieval tools.

Every helper returns an ``EvidenceCandidate`` whose provenance includes the
repository, full commit SHA, POSIX path, 1-based line span, excerpt, retrieval
method, and ``verified=True``. Helpers never synthesize paths outside the
repository and always bound excerpts to the contract limit.
"""

from __future__ import annotations

from codeatlas.domain.agent_models import EvidenceCandidate, EvidenceKind
from codeatlas.domain.indexing_models import IndexedSymbol, SymbolId
from codeatlas.domain.repository_models import (
    CommitInfo,
    RepositoryDescriptor,
    RepositoryPath,
)
from codeatlas.domain.retrieval_models import IndexedRelation, RelationKind

_MAX_EXCERPT_CHARS = 8_000
_DEFAULT_EXCERPT = "unavailable"


def _bounded(value: str) -> str:
    if not value.strip():
        return _DEFAULT_EXCERPT
    return value[:_MAX_EXCERPT_CHARS]


def make_evidence(
    *,
    kind: EvidenceKind,
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    path: RepositoryPath,
    start_line: int,
    end_line: int,
    excerpt: str,
    retrieval_method: str,
    confidence: float = 1.0,
    symbol_id: str | None = None,
    symbol_name: str | None = None,
) -> EvidenceCandidate:
    """Build one verified evidence candidate from explicit provenance."""
    start = max(1, start_line)
    end = max(start, end_line)
    return EvidenceCandidate.create(
        kind=kind,
        repository_id=descriptor.repository_id,
        commit_sha=commit_sha,
        path=path,
        start_line=start,
        end_line=end,
        excerpt=_bounded(excerpt),
        retrieval_method=retrieval_method,
        confidence=confidence,
        verified=True,
        symbol_id=SymbolId(symbol_id) if symbol_id is not None else None,
        symbol_name=symbol_name,
    )


def symbol_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    symbol: IndexedSymbol,
    path: RepositoryPath,
    *,
    retrieval_method: str,
    excerpt_prefix: str = "",
) -> EvidenceCandidate:
    """Return evidence for one indexed declaration span."""
    start, end = (max(1, symbol.span.start_line), max(1, symbol.span.end_line))
    return make_evidence(
        kind=EvidenceKind.SYMBOL,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=path,
        start_line=start,
        end_line=max(start, end),
        excerpt=f"{excerpt_prefix}{symbol.signature or symbol.name}",
        retrieval_method=retrieval_method,
        confidence=symbol.confidence,
        symbol_id=symbol.symbol_id.value,
        symbol_name=symbol.name,
    )


def relation_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    relation: IndexedRelation,
    *,
    retrieval_method: str,
    excerpt_prefix: str = "",
) -> EvidenceCandidate:
    """Return evidence for one parser-derived relation span."""
    kind = _relation_evidence_kind(relation.relation_kind)
    return make_evidence(
        kind=kind,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=relation.path,
        start_line=relation.span.start_line,
        end_line=relation.span.end_line,
        excerpt=f"{excerpt_prefix}{relation.raw_target}",
        retrieval_method=retrieval_method,
        confidence=relation.confidence,
        symbol_id=(
            relation.source_symbol_id.value if relation.source_symbol_id is not None else None
        ),
    )


def _relation_evidence_kind(kind: RelationKind) -> EvidenceKind:
    if kind is RelationKind.IMPORT:
        return EvidenceKind.IMPORT
    if kind is RelationKind.EXPORT:
        return EvidenceKind.FILE_EXCERPT
    return EvidenceKind.REFERENCE


def span_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    path: RepositoryPath,
    start_line: int,
    end_line: int,
    excerpt: str,
    *,
    retrieval_method: str,
    confidence: float = 1.0,
) -> EvidenceCandidate:
    """Return file-excerpt evidence for a 1-based line span."""
    return make_evidence(
        kind=EvidenceKind.FILE_EXCERPT,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=path,
        start_line=start_line,
        end_line=end_line,
        excerpt=excerpt,
        retrieval_method=retrieval_method,
        confidence=confidence,
    )


def text_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    path: RepositoryPath,
    line: int,
    excerpt: str,
    *,
    retrieval_method: str,
) -> EvidenceCandidate:
    """Return evidence for one lexical text match on a single line."""
    return span_evidence(
        descriptor,
        commit_sha,
        path,
        line,
        line,
        excerpt,
        retrieval_method=retrieval_method,
    )


def commit_evidence(
    descriptor: RepositoryDescriptor,
    commit: CommitInfo,
    *,
    retrieval_method: str,
    path: RepositoryPath | None = None,
) -> EvidenceCandidate:
    """Return evidence for commit metadata pinned to the commit itself."""
    return make_evidence(
        kind=EvidenceKind.COMMIT,
        descriptor=descriptor,
        commit_sha=commit.commit_sha,
        path=path or RepositoryPath.root(),
        start_line=1,
        end_line=1,
        excerpt=commit.subject or commit.commit_sha,
        retrieval_method=retrieval_method,
    )


def tree_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    path: RepositoryPath,
    *,
    retrieval_method: str,
) -> EvidenceCandidate:
    """Return evidence for one commit tree entry."""
    excerpt = path.as_posix() or path.value.name or "."
    return make_evidence(
        kind=EvidenceKind.TREE_ENTRY,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=path,
        start_line=1,
        end_line=1,
        excerpt=excerpt,
        retrieval_method=retrieval_method,
    )


def diff_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    text: str,
    *,
    retrieval_method: str,
) -> EvidenceCandidate | None:
    """Return one bounded evidence item for a textual diff, if non-empty."""
    if not text.strip():
        return None
    return make_evidence(
        kind=EvidenceKind.FILE_EXCERPT,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=RepositoryPath.root(),
        start_line=1,
        end_line=1,
        excerpt=text[:2_000],
        retrieval_method=retrieval_method,
    )
