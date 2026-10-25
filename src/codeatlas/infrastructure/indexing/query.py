"""Read-only, bounded queries over the active SQLite index generation.

The adapter opens its own connection pool with ``PRAGMA query_only=ON`` so the
retrieval path can never mutate the index. Every public method returns domain
models only; SQLAlchemy rows, SQL text, databases paths, and source failures are
never exposed to callers.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
from typing import TypeVar

from sqlalchemy import Text, cast, create_engine, event, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from codeatlas.domain.errors import IndexQueryError
from codeatlas.domain.indexing_models import (
    ContentSecurityStatus,
    FileVersion,
    IndexedFile,
    IndexedFileId,
    IndexedSymbol,
    IndexGenerationId,
    IndexGenerationStatus,
    SourceSpan,
    SymbolId,
    SymbolKind,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.domain.retrieval_models import (
    IndexedRelation,
    IndexLexicalHit,
    IndexSnapshotSummary,
    IndexTextHit,
    RelationKind,
    RetrievalLimits,
    SymbolQuery,
)
from codeatlas.infrastructure.indexing.fts import search_fts
from codeatlas.infrastructure.indexing.models import (
    ExportRelationRow,
    FileVersionRow,
    GenerationRow,
    ImportRelationRow,
    IndexedFileRow,
    ReferenceRelationRow,
    RelationMixin,
    SymbolRow,
)

T = TypeVar("T")

_MAX_LISTED_FILES = 10_000
_MAX_SEARCH_FILES = 5_000
_MAX_MATCH_PATTERN_CHARS = 1_024
_MAX_SOURCE_TARGET_CHARS = 1_024

_CALL_STORED = ("call",)
_EXTENDS_STORED = ("extends", "extends_clause", "class_heritage")
_IMPLEMENTS_STORED = ("implements", "implements_clause")
_REFERENCE_STORED = ("reference",)
_GENERATION_REQUIRED = "The index generation is unavailable."
_QUERY_FAILED = "The code index could not answer the query."


def _normalized_prefix(raw: str | None) -> str | None:
    if raw is None:
        return None
    prefix = raw.strip().rstrip("/")
    if prefix in {"", "."}:
        return None
    return prefix


def _path_prefix_clause(prefix: str) -> ColumnElement[bool]:
    """Match one normalized POSIX path prefix without treating it as a pattern."""
    return or_(
        IndexedFileRow.path == prefix,
        IndexedFileRow.path.startswith(f"{prefix}/", autoescape=True),
    )


def _target_clause(column: InstrumentedAttribute[str], raw_target: str) -> ColumnElement[bool]:
    """Match an exact target, its quoted import spelling, or a dotted/space suffix."""
    target = raw_target[:_MAX_SOURCE_TARGET_CHARS]
    quoted = f'"{target}"'
    return or_(
        column.in_((target, quoted)),
        column.endswith(f".{target}", autoescape=True),
        column.endswith(f" {target}", autoescape=True),
    )


def _bounded_excerpt(text: str, focus: int, maximum: int) -> str:
    """Return a non-empty excerpt window around a match focus offset."""
    if len(text) <= maximum:
        candidate = text
    else:
        start = max(0, focus - maximum // 2)
        candidate = text[start : start + maximum]
    if not candidate.strip():
        candidate = text[max(0, focus) : max(0, focus) + maximum]
    return candidate or " "


class SqliteIndexQuery:
    """Implement ``IndexQueryPort`` against one migrated SQLite index database."""

    def __init__(self, database_url: str, *, limits: RetrievalLimits | None = None) -> None:
        self._limits = limits or RetrievalLimits()
        self._engine: Engine = create_engine(
            database_url, connect_args={"check_same_thread": False}
        )
        event.listen(self._engine, "connect", self._configure_connection)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA query_only=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    async def aclose(self) -> None:
        """Release pooled connections without raising on a second call."""
        await asyncio.to_thread(self._engine.dispose)

    async def _run(self, operation: Callable[[Session], T]) -> T:
        try:
            return await asyncio.to_thread(self._run_sync, operation)
        except IndexQueryError:
            raise
        except SQLAlchemyError as error:
            raise IndexQueryError(_QUERY_FAILED, internal_cause=error) from error
        except ValueError as error:
            raise IndexQueryError(
                "The code index content is unusable.", internal_cause=error
            ) from error

    def _run_sync(self, operation: Callable[[Session], T]) -> T:
        with self._sessions() as session:
            return operation(session)

    @staticmethod
    def _generation_id(value: str) -> IndexGenerationId:
        try:
            return IndexGenerationId(value)
        except ValueError as error:
            raise IndexQueryError(
                "The index generation is invalid.", internal_cause=error
            ) from error

    @staticmethod
    def _limit(value: int, *, maximum: int) -> int:
        if not 1 <= value <= maximum:
            raise IndexQueryError("The query result limit is invalid.")
        return value

    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary | None:
        def operation(session: Session) -> IndexSnapshotSummary | None:
            row = session.scalar(
                select(GenerationRow).where(
                    GenerationRow.repository_id == repository_id.value,
                    GenerationRow.status == IndexGenerationStatus.ACTIVE.value,
                )
            )
            if row is None:
                return None
            return IndexSnapshotSummary(
                RepositoryId(row.repository_id),
                row.commit_sha,
                row.generation_id,
                row.generation_number,
                IndexGenerationStatus(row.status),
            )

        return await self._run(operation)

    async def list_files(self, generation_id: str) -> tuple[IndexedFile, ...]:
        generation = self._generation_id(generation_id)

        def operation(session: Session) -> tuple[IndexedFile, ...]:
            rows = session.execute(
                select(IndexedFileRow, FileVersionRow)
                .join(FileVersionRow, FileVersionRow.file_row_id == IndexedFileRow.row_id)
                .where(IndexedFileRow.generation_id == generation.value)
                .order_by(IndexedFileRow.path)
            ).all()
            if len(rows) > _MAX_LISTED_FILES:
                raise IndexQueryError("The index generation has too many files to list.")
            return tuple(
                self._file_from_rows(generation, file_row, version_row)
                for file_row, version_row in rows
            )

        return await self._run(operation)

    async def get_file(self, generation_id: str, path: RepositoryPath) -> IndexedFile | None:
        generation = self._generation_id(generation_id)

        def operation(session: Session) -> IndexedFile | None:
            row = session.execute(
                select(IndexedFileRow, FileVersionRow)
                .join(FileVersionRow, FileVersionRow.file_row_id == IndexedFileRow.row_id)
                .where(
                    IndexedFileRow.generation_id == generation.value,
                    IndexedFileRow.path == path.as_posix(),
                )
            ).one_or_none()
            if row is None:
                return None
            return self._file_from_rows(generation, row[0], row[1])

        return await self._run(operation)

    async def list_file_paths(
        self,
        generation_id: str,
        *,
        file_ids: Sequence[IndexedFileId] | None = None,
    ) -> dict[IndexedFileId, RepositoryPath]:
        """Adapter extension: map logical file IDs to paths without loading content.

        Passing ``file_ids`` keeps the lookup bounded to the files a caller already
        referenced, so large generations do not need a full path map per tool call.
        """
        generation = self._generation_id(generation_id)
        selected = None if file_ids is None else tuple(item.value for item in file_ids)
        if selected is not None and not selected:
            return {}

        def operation(session: Session) -> dict[IndexedFileId, RepositoryPath]:
            statement = select(IndexedFileRow.logical_file_id, IndexedFileRow.path).where(
                IndexedFileRow.generation_id == generation.value
            )
            if selected is not None:
                statement = statement.where(IndexedFileRow.logical_file_id.in_(selected))
            rows = session.execute(statement.order_by(IndexedFileRow.path)).all()
            if len(rows) > _MAX_LISTED_FILES:
                raise IndexQueryError("The index generation has too many files to list.")
            return {
                IndexedFileId(logical_file_id): RepositoryPath(PurePosixPath(path))
                for logical_file_id, path in rows
            }

        return await self._run(operation)

    async def find_symbols(
        self, generation_id: str, query: SymbolQuery
    ) -> tuple[IndexedSymbol, ...]:
        generation = self._generation_id(generation_id)
        limit = min(query.limit, self._limits.max_results)

        def operation(session: Session) -> tuple[IndexedSymbol, ...]:
            statement = (
                select(SymbolRow, IndexedFileRow)
                .join(IndexedFileRow, IndexedFileRow.row_id == SymbolRow.file_row_id)
                .where(SymbolRow.generation_id == generation.value)
            )
            if query.name is not None:
                statement = statement.where(SymbolRow.name == query.name)
            if query.qualified_name is not None:
                target = query.qualified_name
                statement = statement.where(
                    or_(
                        SymbolRow.qualified_name == target,
                        SymbolRow.qualified_name.endswith(f".{target}", autoescape=True),
                    )
                )
            if query.kinds:
                statement = statement.where(
                    SymbolRow.kind.in_([kind.value for kind in query.kinds])
                )
            prefix = _normalized_prefix(query.path_prefix)
            if prefix is not None:
                statement = statement.where(_path_prefix_clause(prefix))
            statement = statement.order_by(IndexedFileRow.path, SymbolRow.start_byte).limit(limit)
            return tuple(
                self._symbol_from_rows(symbol_row, file_row)
                for symbol_row, file_row in session.execute(statement).all()
            )

        return await self._run(operation)

    async def list_symbols(
        self, generation_id: str, *, path: RepositoryPath | None, limit: int
    ) -> tuple[IndexedSymbol, ...]:
        generation = self._generation_id(generation_id)
        effective = min(self._limit(limit, maximum=1_000), self._limits.max_results)

        def operation(session: Session) -> tuple[IndexedSymbol, ...]:
            statement = (
                select(SymbolRow, IndexedFileRow)
                .join(IndexedFileRow, IndexedFileRow.row_id == SymbolRow.file_row_id)
                .where(SymbolRow.generation_id == generation.value)
            )
            if path is not None:
                statement = statement.where(IndexedFileRow.path == path.as_posix())
            statement = statement.order_by(IndexedFileRow.path, SymbolRow.start_byte).limit(
                effective
            )
            return tuple(
                self._symbol_from_rows(symbol_row, file_row)
                for symbol_row, file_row in session.execute(statement).all()
            )

        return await self._run(operation)

    async def get_symbol(self, generation_id: str, symbol_id: SymbolId) -> IndexedSymbol | None:
        generation = self._generation_id(generation_id)

        def operation(session: Session) -> IndexedSymbol | None:
            row = session.execute(
                select(SymbolRow, IndexedFileRow)
                .join(IndexedFileRow, IndexedFileRow.row_id == SymbolRow.file_row_id)
                .where(
                    SymbolRow.generation_id == generation.value,
                    SymbolRow.symbol_id == symbol_id.value,
                )
            ).one_or_none()
            if row is None:
                return None
            return self._symbol_from_rows(row[0], row[1])

        return await self._run(operation)

    async def search_text(
        self,
        generation_id: str,
        *,
        pattern: str,
        case_sensitive: bool,
        limit: int,
    ) -> tuple[IndexTextHit, ...]:
        """Search stored safe content line-by-line with a literal substring.

        The SQL prefilter lowercases ASCII only, so case-insensitive matching of
        non-ASCII characters is limited by SQLite; every candidate is re-verified
        in Python before it becomes a hit.
        """
        generation = self._generation_id(generation_id)
        effective = min(self._limit(limit, maximum=1_000), self._limits.max_results)
        if not pattern or "\x00" in pattern or len(pattern) > _MAX_MATCH_PATTERN_CHARS:
            raise IndexQueryError("The search pattern is invalid.")
        expression = (
            re.compile(re.escape(pattern))
            if case_sensitive
            else re.compile(re.escape(pattern), re.IGNORECASE)
        )

        def operation(session: Session) -> tuple[IndexTextHit, ...]:
            generation_row = session.get(GenerationRow, generation.value)
            if generation_row is None:
                raise IndexQueryError(_GENERATION_REQUIRED)
            repository_id = RepositoryId(generation_row.repository_id)
            commit_sha = generation_row.commit_sha
            content_text = cast(FileVersionRow.content_utf8, Text)
            if case_sensitive:
                predicate = func.instr(content_text, pattern) > 0
            else:
                predicate = func.instr(func.lower(content_text), pattern.lower()) > 0
            statement = (
                select(IndexedFileRow.path, FileVersionRow.content_utf8)
                .join(FileVersionRow, FileVersionRow.file_row_id == IndexedFileRow.row_id)
                .where(
                    IndexedFileRow.generation_id == generation.value,
                    FileVersionRow.content_utf8.is_not(None),
                    predicate,
                )
                .order_by(IndexedFileRow.path)
            )
            hits: list[IndexTextHit] = []
            scanned = 0
            for path_text, content in session.execute(statement).all():
                scanned += 1
                if scanned > _MAX_SEARCH_FILES:
                    break
                if content is None:
                    continue
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise IndexQueryError(
                        "The code index content is unusable.", internal_cause=error
                    ) from error
                path = RepositoryPath(PurePosixPath(path_text))
                for line_number, line in enumerate(text.splitlines(), start=1):
                    for match in expression.finditer(line):
                        start = match.start()
                        end = match.end()
                        excerpt = _bounded_excerpt(line, start, self._limits.max_excerpt_chars)
                        if not excerpt.strip():
                            excerpt = match.group(0)[: self._limits.max_excerpt_chars] or " "
                        hits.append(
                            IndexTextHit(
                                repository_id,
                                commit_sha,
                                path,
                                line_number,
                                excerpt,
                                start,
                                end,
                            )
                        )
                        if len(hits) >= effective:
                            return tuple(hits)
            return tuple(hits)

        return await self._run(operation)

    async def search_lexical(
        self,
        generation_id: str,
        *,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> tuple[IndexLexicalHit, ...]:
        """Return BM25-ranked FTS5 line matches for one generation."""
        generation = self._generation_id(generation_id)
        effective = min(self._limit(limit, maximum=1_000), self._limits.max_results)
        if not query.strip() or "\x00" in query or len(query) > _MAX_MATCH_PATTERN_CHARS:
            raise IndexQueryError("The lexical query is invalid.")
        prefix = _normalized_prefix(path_prefix)

        def operation(session: Session) -> tuple[IndexLexicalHit, ...]:
            rows = search_fts(
                session.connection(),
                generation_id=generation.value,
                query=query,
                limit=effective,
                path_prefix=prefix,
            )
            hits: list[IndexLexicalHit] = []
            for row in rows:
                try:
                    path = (
                        RepositoryPath(PurePosixPath(row.path))
                        if row.path
                        else RepositoryPath.root()
                    )
                    hits.append(
                        IndexLexicalHit(
                            repository_id=RepositoryId(row.repository_id),
                            commit_sha=row.commit_sha,
                            path=path,
                            line=row.line,
                            excerpt=row.excerpt,
                            score=row.score,
                        )
                    )
                except ValueError as error:
                    raise IndexQueryError(
                        "The lexical index returned an unusable row.",
                        internal_cause=error,
                    ) from error
            return tuple(hits)

        return await self._run(operation)

    async def find_relations(
        self,
        generation_id: str,
        *,
        relation_kind: RelationKind | None,
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        limit: int,
    ) -> tuple[IndexedRelation, ...]:
        effective = min(self._limit(limit, maximum=1_000), self._limits.max_results)
        kinds = None if relation_kind is None else (relation_kind,)
        return await self._relation_query(
            generation_id,
            kinds=kinds,
            raw_target=raw_target,
            source_symbol_id=source_symbol_id,
            path=None,
            limit=effective,
        )

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
        """Adapter extension: one bounded relation query with every filter combined."""
        effective = min(self._limit(limit, maximum=1_000), self._limits.max_results)
        return await self._relation_query(
            generation_id,
            kinds=relation_kinds,
            raw_target=raw_target,
            source_symbol_id=source_symbol_id,
            path=path,
            limit=effective,
        )

    async def _relation_query(
        self,
        generation_id: str,
        *,
        kinds: tuple[RelationKind, ...] | None,
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        path: RepositoryPath | None,
        limit: int,
    ) -> tuple[IndexedRelation, ...]:
        generation = self._generation_id(generation_id)
        selected = set(kinds) if kinds is not None else set(RelationKind)

        def operation(session: Session) -> tuple[IndexedRelation, ...]:
            generation_row = session.get(GenerationRow, generation.value)
            if generation_row is None:
                raise IndexQueryError(_GENERATION_REQUIRED)
            repository_id = RepositoryId(generation_row.repository_id)
            commit_sha = generation_row.commit_sha
            prefix = _normalized_prefix(None if path is None else path.as_posix())
            path_clause = _path_prefix_clause(prefix) if prefix is not None else None
            collected: list[tuple[str, int, IndexedRelation]] = []

            if RelationKind.IMPORT in selected:
                collected.extend(
                    self._collect_imports(
                        session,
                        generation,
                        path_clause,
                        raw_target,
                        source_symbol_id,
                        limit,
                        repository_id,
                        commit_sha,
                    )
                )
            if RelationKind.EXPORT in selected:
                collected.extend(
                    self._collect_exports(
                        session,
                        generation,
                        path_clause,
                        raw_target,
                        source_symbol_id,
                        limit,
                        repository_id,
                        commit_sha,
                    )
                )
            reference_kinds = selected.intersection(
                {
                    RelationKind.CALL,
                    RelationKind.EXTENDS,
                    RelationKind.IMPLEMENTS,
                    RelationKind.REFERENCE,
                }
            )
            if reference_kinds:
                collected.extend(
                    self._collect_references(
                        session,
                        generation,
                        path_clause,
                        reference_kinds,
                        raw_target,
                        source_symbol_id,
                        limit,
                        repository_id,
                        commit_sha,
                    )
                )
            collected.sort(
                key=lambda item: (item[0], item[1], item[2].relation_kind.value, item[2].raw_target)
            )
            return tuple(relation for _path, _offset, relation in collected[:limit])

        return await self._run(operation)

    def _collect_imports(
        self,
        session: Session,
        generation: IndexGenerationId,
        path_clause: ColumnElement[bool] | None,
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        limit: int,
        repository_id: RepositoryId,
        commit_sha: str,
    ) -> list[tuple[str, int, IndexedRelation]]:
        statement = (
            select(ImportRelationRow, IndexedFileRow)
            .join(IndexedFileRow, IndexedFileRow.row_id == ImportRelationRow.file_row_id)
            .where(ImportRelationRow.generation_id == generation.value)
        )
        if path_clause is not None:
            statement = statement.where(path_clause)
        if raw_target is not None:
            statement = statement.where(_target_clause(ImportRelationRow.raw_target, raw_target))
        if source_symbol_id is not None:
            statement = statement.where(
                ImportRelationRow.source_symbol_id == source_symbol_id.value
            )
        statement = statement.order_by(IndexedFileRow.path, ImportRelationRow.start_byte).limit(
            limit
        )
        results: list[tuple[str, int, IndexedRelation]] = []
        for relation_row, file_row in session.execute(statement).all():
            relation = self._relation_from_rows(
                repository_id, commit_sha, relation_row, file_row, RelationKind.IMPORT
            )
            results.append((relation.path.as_posix(), relation.span.start_byte, relation))
        return results

    def _collect_exports(
        self,
        session: Session,
        generation: IndexGenerationId,
        path_clause: ColumnElement[bool] | None,
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        limit: int,
        repository_id: RepositoryId,
        commit_sha: str,
    ) -> list[tuple[str, int, IndexedRelation]]:
        statement = (
            select(ExportRelationRow, IndexedFileRow)
            .join(IndexedFileRow, IndexedFileRow.row_id == ExportRelationRow.file_row_id)
            .where(ExportRelationRow.generation_id == generation.value)
        )
        if path_clause is not None:
            statement = statement.where(path_clause)
        if raw_target is not None:
            statement = statement.where(_target_clause(ExportRelationRow.raw_target, raw_target))
        if source_symbol_id is not None:
            statement = statement.where(
                ExportRelationRow.source_symbol_id == source_symbol_id.value
            )
        statement = statement.order_by(IndexedFileRow.path, ExportRelationRow.start_byte).limit(
            limit
        )
        results: list[tuple[str, int, IndexedRelation]] = []
        for relation_row, file_row in session.execute(statement).all():
            relation = self._relation_from_rows(
                repository_id, commit_sha, relation_row, file_row, RelationKind.EXPORT
            )
            results.append((relation.path.as_posix(), relation.span.start_byte, relation))
        return results

    def _collect_references(
        self,
        session: Session,
        generation: IndexGenerationId,
        path_clause: ColumnElement[bool] | None,
        kinds: set[RelationKind],
        raw_target: str | None,
        source_symbol_id: SymbolId | None,
        limit: int,
        repository_id: RepositoryId,
        commit_sha: str,
    ) -> list[tuple[str, int, IndexedRelation]]:
        stored_filters: list[ColumnElement[bool]] = []
        if RelationKind.CALL in kinds:
            stored_filters.append(ReferenceRelationRow.relation_kind.in_(_CALL_STORED))
        if RelationKind.EXTENDS in kinds:
            stored_filters.append(ReferenceRelationRow.relation_kind.in_(_EXTENDS_STORED))
        if RelationKind.IMPLEMENTS in kinds:
            stored_filters.append(ReferenceRelationRow.relation_kind.in_(_IMPLEMENTS_STORED))
        if RelationKind.REFERENCE in kinds:
            stored_filters.append(ReferenceRelationRow.relation_kind.in_(_REFERENCE_STORED))
        statement = (
            select(ReferenceRelationRow, IndexedFileRow)
            .join(IndexedFileRow, IndexedFileRow.row_id == ReferenceRelationRow.file_row_id)
            .where(
                ReferenceRelationRow.generation_id == generation.value,
                or_(*stored_filters),
            )
        )
        if path_clause is not None:
            statement = statement.where(path_clause)
        if raw_target is not None:
            statement = statement.where(_target_clause(ReferenceRelationRow.raw_target, raw_target))
        if source_symbol_id is not None:
            statement = statement.where(
                ReferenceRelationRow.source_symbol_id == source_symbol_id.value
            )
        statement = statement.order_by(IndexedFileRow.path, ReferenceRelationRow.start_byte).limit(
            limit
        )
        results: list[tuple[str, int, IndexedRelation]] = []
        for relation_row, file_row in session.execute(statement).all():
            stored_kind = relation_row.relation_kind
            if stored_kind == "call":
                kind = RelationKind.CALL
            elif stored_kind in _EXTENDS_STORED:
                kind = RelationKind.EXTENDS
            elif stored_kind in _IMPLEMENTS_STORED:
                kind = RelationKind.IMPLEMENTS
            else:
                kind = RelationKind.REFERENCE
            relation = self._relation_from_rows(
                repository_id, commit_sha, relation_row, file_row, kind
            )
            results.append((relation.path.as_posix(), relation.span.start_byte, relation))
        return results

    @staticmethod
    def _file_from_rows(
        generation: IndexGenerationId,
        file_row: IndexedFileRow,
        version_row: FileVersionRow,
    ) -> IndexedFile:
        file_id = IndexedFileId(file_row.logical_file_id)
        return IndexedFile(
            file_id=file_id,
            generation_id=generation,
            path=RepositoryPath(PurePosixPath(file_row.path)),
            version=FileVersion(
                file_id,
                version_row.commit_sha,
                version_row.git_object_id,
                version_row.content_hash,
            ),
            byte_size=file_row.byte_size,
            line_count=file_row.line_count,
            language=file_row.language,
            encoding=file_row.encoding,
            security_status=ContentSecurityStatus(file_row.security_status),
            status_reason=file_row.status_reason,
            content=(
                version_row.content_utf8.decode("utf-8")
                if version_row.content_utf8 is not None
                else None
            ),
            parser_id=file_row.parser_id,
            parser_version=file_row.parser_version,
            grammar_version=file_row.grammar_version,
            syntax_error_count=file_row.syntax_error_count,
            reused=file_row.reused,
        )

    @staticmethod
    def _symbol_from_rows(symbol_row: SymbolRow, file_row: IndexedFileRow) -> IndexedSymbol:
        return IndexedSymbol(
            symbol_id=SymbolId(symbol_row.symbol_id),
            generation_id=IndexGenerationId(symbol_row.generation_id),
            file_id=IndexedFileId(file_row.logical_file_id),
            kind=SymbolKind(symbol_row.kind),
            name=symbol_row.name,
            qualified_name=symbol_row.qualified_name,
            signature=symbol_row.signature,
            span=SourceSpan(
                symbol_row.start_byte,
                symbol_row.end_byte,
                symbol_row.start_line,
                symbol_row.start_column,
                symbol_row.end_line,
                symbol_row.end_column,
            ),
            parent_symbol_id=(
                SymbolId(symbol_row.parent_symbol_id)
                if symbol_row.parent_symbol_id is not None
                else None
            ),
            exported=symbol_row.exported,
            has_documentation=symbol_row.has_documentation,
            parser_id=symbol_row.parser_id,
            parser_version=symbol_row.parser_version,
            grammar_version=symbol_row.grammar_version,
            confidence=symbol_row.confidence,
            derivation_kind=symbol_row.derivation_kind,
        )

    @staticmethod
    def _relation_from_rows(
        repository_id: RepositoryId,
        commit_sha: str,
        relation_row: RelationMixin,
        file_row: IndexedFileRow,
        kind: RelationKind,
    ) -> IndexedRelation:
        return IndexedRelation(
            repository_id=repository_id,
            commit_sha=commit_sha,
            path=RepositoryPath(PurePosixPath(file_row.path)),
            relation_kind=kind,
            raw_target=relation_row.raw_target,
            span=SourceSpan(
                relation_row.start_byte,
                relation_row.end_byte,
                relation_row.start_line,
                relation_row.start_column,
                relation_row.end_line,
                relation_row.end_column,
            ),
            source_symbol_id=(
                SymbolId(relation_row.source_symbol_id)
                if relation_row.source_symbol_id is not None
                else None
            ),
            confidence=relation_row.confidence,
        )
