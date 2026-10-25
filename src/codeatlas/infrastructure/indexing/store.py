"""Thread-offloaded SQLAlchemy implementation of the index store port."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import cast

from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from codeatlas.domain.errors import (
    IndexBusyError,
    IndexingError,
    IndexSchemaError,
    IndexValidationError,
    NotFoundError,
)
from codeatlas.domain.indexing_models import (
    ContentSecurityStatus,
    ExportRelation,
    FileVersion,
    ImportRelation,
    IndexedFile,
    IndexedFileId,
    IndexedSymbol,
    IndexGenerationId,
    IndexGenerationStatus,
    IndexingFailure,
    IndexingStatistics,
    ReferenceRelation,
    RelationBase,
    RepositorySnapshot,
    SourceSpan,
    SymbolId,
    SymbolKind,
    validate_generation_transition,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.infrastructure.indexing.fts import (
    copy_file_fts,
    delete_generation_fts,
    insert_file_fts,
)
from codeatlas.infrastructure.indexing.models import (
    ExportRelationRow,
    FileVersionRow,
    GenerationRow,
    ImportRelationRow,
    IndexedFileRow,
    IndexJobRow,
    ReferenceRelationRow,
    RelationMixin,
    RepositoryRow,
    SnapshotRow,
    SymbolRow,
    ValidationResultRow,
)

SCHEMA_REVISION = "0003_m6_fts"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _span_from_row(row: SymbolRow) -> SourceSpan:
    return SourceSpan(
        row.start_byte,
        row.end_byte,
        row.start_line,
        row.start_column,
        row.end_line,
        row.end_column,
    )


class SqliteIndexStore:
    """Persist immutable generations with an atomic active pointer transition."""

    def __init__(self, database_url: str, *, busy_timeout_seconds: float = 5.0) -> None:
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": busy_timeout_seconds},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    async def assert_schema_current(self) -> None:
        await asyncio.to_thread(self._assert_schema_current)

    def _assert_schema_current(self) -> None:
        try:
            with self.engine.connect() as connection:
                revision = connection.execute(text("SELECT version_num FROM alembic_version"))
                if revision.scalar_one_or_none() != SCHEMA_REVISION:
                    raise IndexSchemaError()
        except IndexSchemaError:
            raise
        except SQLAlchemyError as error:
            raise IndexSchemaError(internal_cause=error) from error

    async def acquire_lease(
        self, repository_id: RepositoryId, owner_token: str, expires_at: datetime
    ) -> None:
        await asyncio.to_thread(self._acquire_lease, repository_id, owner_token, expires_at)

    def _acquire_lease(
        self, repository_id: RepositoryId, owner_token: str, expires_at: datetime
    ) -> None:
        now = _now()
        try:
            with self._sessions.begin() as session:
                session.merge(RepositoryRow(repository_id=repository_id.value, created_at=now))
                existing = session.get(IndexJobRow, repository_id.value)
                if existing is not None and existing.lease_expires_at > now:
                    raise IndexBusyError()
                if existing is not None:
                    session.delete(existing)
                    session.flush()
                session.add(
                    IndexJobRow(
                        repository_id=repository_id.value,
                        owner_token=owner_token,
                        lease_expires_at=expires_at.astimezone(UTC).isoformat(),
                        heartbeat_at=now,
                    )
                )
        except IndexBusyError:
            raise
        except (IntegrityError, SQLAlchemyError) as error:
            raise IndexBusyError(internal_cause=error) from error

    async def release_lease(self, repository_id: RepositoryId, owner_token: str) -> None:
        await asyncio.to_thread(self._release_lease, repository_id, owner_token)

    def _release_lease(self, repository_id: RepositoryId, owner_token: str) -> None:
        with self._sessions.begin() as session:
            row = session.get(IndexJobRow, repository_id.value)
            if row is not None and row.owner_token == owner_token:
                session.delete(row)

    async def create_generation(
        self,
        repository_id: RepositoryId,
        commit_sha: str,
        parser_fingerprint: str,
        policy_fingerprint: str,
        owner_token: str,
        parser_metadata: tuple[str, ...] = (),
    ) -> RepositorySnapshot:
        return await asyncio.to_thread(
            self._create_generation,
            repository_id,
            commit_sha,
            parser_fingerprint,
            policy_fingerprint,
            owner_token,
            parser_metadata,
        )

    def _create_generation(
        self,
        repository_id: RepositoryId,
        commit_sha: str,
        parser_fingerprint: str,
        policy_fingerprint: str,
        owner_token: str,
        parser_metadata: tuple[str, ...],
    ) -> RepositorySnapshot:
        generation_id = IndexGenerationId(f"gen_{uuid.uuid4().hex}")
        timestamp = _now()
        with self._sessions.begin() as session:
            number = (
                session.scalar(
                    select(func.max(GenerationRow.generation_number)).where(
                        GenerationRow.repository_id == repository_id.value
                    )
                )
                or 0
            ) + 1
            session.add(
                GenerationRow(
                    generation_id=generation_id.value,
                    repository_id=repository_id.value,
                    commit_sha=commit_sha,
                    generation_number=number,
                    status=IndexGenerationStatus.PENDING.value,
                    owner_token=owner_token,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.flush()
            session.add(
                SnapshotRow(
                    repository_id=repository_id.value,
                    commit_sha=commit_sha,
                    generation_id=generation_id.value,
                    parser_fingerprint=parser_fingerprint,
                    policy_fingerprint=policy_fingerprint,
                    parser_metadata_json=json.dumps(parser_metadata),
                )
            )
        return RepositorySnapshot(
            repository_id,
            commit_sha,
            generation_id,
            number,
            parser_fingerprint,
            policy_fingerprint,
            parser_metadata,
        )

    async def set_status(
        self, generation_id: IndexGenerationId, target: IndexGenerationStatus
    ) -> None:
        await asyncio.to_thread(self._set_status, generation_id, target)

    def _set_status(self, generation_id: IndexGenerationId, target: IndexGenerationStatus) -> None:
        with self._sessions.begin() as session:
            row = session.get(GenerationRow, generation_id.value)
            if row is None:
                raise NotFoundError("The index generation was not found.")
            validate_generation_transition(IndexGenerationStatus(row.status), target)
            row.status = target.value
            row.updated_at = _now()

    async def add_file_bundle(
        self,
        file: IndexedFile,
        symbols: tuple[IndexedSymbol, ...],
        imports: tuple[ImportRelation, ...],
        exports: tuple[ExportRelation, ...],
        references: tuple[ReferenceRelation, ...],
    ) -> None:
        await asyncio.to_thread(self._add_file_bundle, file, symbols, imports, exports, references)

    def _add_file_bundle(
        self,
        file: IndexedFile,
        symbols: tuple[IndexedSymbol, ...],
        imports: tuple[ImportRelation, ...],
        exports: tuple[ExportRelation, ...],
        references: tuple[ReferenceRelation, ...],
    ) -> None:
        try:
            with self._sessions.begin() as session:
                file_row = self._insert_file(session, file)
                symbol_rows = [self._symbol_row(symbol, file_row.row_id) for symbol in symbols]
                session.add_all(symbol_rows)
                session.flush()
                session.add_all(
                    [
                        self._relation_row(ImportRelationRow, relation, file_row.row_id)
                        for relation in imports
                    ]
                )
                session.add_all(
                    [
                        self._relation_row(ExportRelationRow, relation, file_row.row_id)
                        for relation in exports
                    ]
                )
                session.add_all(
                    [
                        self._relation_row(
                            ReferenceRelationRow,
                            relation,
                            file_row.row_id,
                            relation_kind=relation.relation_kind,
                        )
                        for relation in references
                    ]
                )
                if file.content is not None:
                    insert_file_fts(
                        session,
                        generation_id=file.generation_id.value,
                        path=file.path.as_posix(),
                        content=file.content,
                    )
        except SQLAlchemyError as error:
            raise IndexingError(
                "Index data could not be persisted.", internal_cause=error
            ) from error

    @staticmethod
    def _insert_file(session: Session, file: IndexedFile) -> IndexedFileRow:
        row = IndexedFileRow(
            logical_file_id=file.file_id.value,
            generation_id=file.generation_id.value,
            path=file.path.as_posix(),
            byte_size=file.byte_size,
            line_count=file.line_count,
            language=file.language,
            encoding=file.encoding,
            security_status=file.security_status.value,
            status_reason=file.status_reason,
            parser_id=file.parser_id,
            parser_version=file.parser_version,
            grammar_version=file.grammar_version,
            syntax_error_count=file.syntax_error_count,
            reused=file.reused,
        )
        session.add(row)
        session.flush()
        session.add(
            FileVersionRow(
                file_row_id=row.row_id,
                commit_sha=file.version.commit_sha,
                git_object_id=file.version.git_object_id,
                content_hash=file.version.content_hash,
                content_utf8=file.content.encode("utf-8") if file.content is not None else None,
            )
        )
        return row

    @staticmethod
    def _symbol_row(symbol: IndexedSymbol, file_row_id: int) -> SymbolRow:
        span = symbol.span
        return SymbolRow(
            symbol_id=symbol.symbol_id.value,
            generation_id=symbol.generation_id.value,
            file_row_id=file_row_id,
            kind=symbol.kind.value,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            signature=symbol.signature,
            start_byte=span.start_byte,
            end_byte=span.end_byte,
            start_line=span.start_line,
            start_column=span.start_column,
            end_line=span.end_line,
            end_column=span.end_column,
            parent_symbol_id=symbol.parent_symbol_id.value if symbol.parent_symbol_id else None,
            exported=symbol.exported,
            has_documentation=symbol.has_documentation,
            parser_id=symbol.parser_id,
            parser_version=symbol.parser_version,
            grammar_version=symbol.grammar_version,
            confidence=symbol.confidence,
            derivation_kind=symbol.derivation_kind,
        )

    @staticmethod
    def _relation_row(
        row_type: type[ImportRelationRow] | type[ExportRelationRow] | type[ReferenceRelationRow],
        relation: RelationBase,
        file_row_id: int,
        **extra: str,
    ) -> ImportRelationRow | ExportRelationRow | ReferenceRelationRow:
        span = relation.span
        return row_type(
            generation_id=relation.generation_id.value,
            file_row_id=file_row_id,
            source_symbol_id=(
                relation.source_symbol_id.value if relation.source_symbol_id else None
            ),
            raw_target=relation.raw_target,
            start_byte=span.start_byte,
            end_byte=span.end_byte,
            start_line=span.start_line,
            start_column=span.start_column,
            end_line=span.end_line,
            end_column=span.end_column,
            resolution_status=relation.resolution_status.value,
            confidence=relation.confidence,
            resolver_metadata=relation.resolver_metadata,
            **extra,
        )

    async def copy_file_bundle(
        self,
        source_generation_id: IndexGenerationId,
        target_generation_id: IndexGenerationId,
        path: RepositoryPath,
        commit_sha: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._copy_file_bundle,
            source_generation_id,
            target_generation_id,
            path,
            commit_sha,
        )

    def _copy_file_bundle(
        self,
        source_generation_id: IndexGenerationId,
        target_generation_id: IndexGenerationId,
        path: RepositoryPath,
        commit_sha: str,
    ) -> bool:
        with self._sessions.begin() as session:
            source = session.scalar(
                select(IndexedFileRow).where(
                    IndexedFileRow.generation_id == source_generation_id.value,
                    IndexedFileRow.path == path.as_posix(),
                )
            )
            target_generation = session.get(GenerationRow, target_generation_id.value)
            if source is None or target_generation is None:
                return False
            version = session.get(FileVersionRow, source.row_id)
            if version is None:
                return False
            domain_file = self._file_from_rows(
                source, version, target_generation_id, commit_sha, True
            )
            target = self._insert_file(session, domain_file)
            old_symbols = session.scalars(
                select(SymbolRow).where(SymbolRow.file_row_id == source.row_id)
            ).all()
            symbol_map: dict[str, SymbolId] = {}
            for old in old_symbols:
                new_id = SymbolId.derive(
                    RepositoryId(target_generation.repository_id),
                    commit_sha,
                    source.language or "unknown",
                    path,
                    SymbolKind(old.kind),
                    old.qualified_name,
                    old.signature,
                    _span_from_row(old),
                )
                symbol_map[old.symbol_id] = new_id
            for old in old_symbols:
                session.add(
                    SymbolRow(
                        symbol_id=symbol_map[old.symbol_id].value,
                        generation_id=target_generation_id.value,
                        file_row_id=target.row_id,
                        kind=old.kind,
                        name=old.name,
                        qualified_name=old.qualified_name,
                        signature=old.signature,
                        start_byte=old.start_byte,
                        end_byte=old.end_byte,
                        start_line=old.start_line,
                        start_column=old.start_column,
                        end_line=old.end_line,
                        end_column=old.end_column,
                        parent_symbol_id=(
                            symbol_map[old.parent_symbol_id].value if old.parent_symbol_id else None
                        ),
                        exported=old.exported,
                        has_documentation=old.has_documentation,
                        parser_id=old.parser_id,
                        parser_version=old.parser_version,
                        grammar_version=old.grammar_version,
                        confidence=old.confidence,
                        derivation_kind=old.derivation_kind,
                    )
                )
            session.flush()
            self._copy_relations(session, source, target, target_generation_id, symbol_map)
            copy_file_fts(
                session,
                source_generation_id=source_generation_id.value,
                target_generation_id=target_generation_id.value,
                path=path.as_posix(),
            )
            return True

    @staticmethod
    def _copy_relations(
        session: Session,
        source: IndexedFileRow,
        target: IndexedFileRow,
        generation_id: IndexGenerationId,
        symbol_map: dict[str, SymbolId],
    ) -> None:
        for row_type in (ImportRelationRow, ExportRelationRow, ReferenceRelationRow):
            for old in session.scalars(
                select(row_type).where(row_type.file_row_id == source.row_id)
            ):
                relation = cast(RelationMixin, old)
                values = {
                    column.name: getattr(relation, column.name)
                    for column in row_type.__table__.columns
                    if column.name != "relation_id"
                }
                values["generation_id"] = generation_id.value
                values["file_row_id"] = target.row_id
                if relation.source_symbol_id:
                    values["source_symbol_id"] = symbol_map[relation.source_symbol_id].value
                session.add(row_type(**values))

    async def list_files(self, generation_id: IndexGenerationId) -> tuple[IndexedFile, ...]:
        return await asyncio.to_thread(self._list_files, generation_id)

    def _list_files(self, generation_id: IndexGenerationId) -> tuple[IndexedFile, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(IndexedFileRow, FileVersionRow)
                .join(FileVersionRow, FileVersionRow.file_row_id == IndexedFileRow.row_id)
                .where(IndexedFileRow.generation_id == generation_id.value)
                .order_by(IndexedFileRow.path)
            ).all()
            return tuple(
                self._file_from_rows(
                    file_row, version, generation_id, version.commit_sha, file_row.reused
                )
                for file_row, version in rows
            )

    @staticmethod
    def _file_from_rows(
        row: IndexedFileRow,
        version: FileVersionRow,
        generation_id: IndexGenerationId,
        commit_sha: str,
        reused: bool,
    ) -> IndexedFile:
        file_id = IndexedFileId(row.logical_file_id)
        return IndexedFile(
            file_id,
            generation_id,
            RepositoryPath(PurePosixPath(row.path)),
            FileVersion(file_id, commit_sha, version.git_object_id, version.content_hash),
            row.byte_size,
            row.line_count,
            row.language,
            row.encoding,
            ContentSecurityStatus(row.security_status),
            row.status_reason,
            version.content_utf8.decode("utf-8") if version.content_utf8 is not None else None,
            row.parser_id,
            row.parser_version,
            row.grammar_version,
            row.syntax_error_count,
            reused,
        )

    async def get_active(self, repository_id: RepositoryId) -> RepositorySnapshot | None:
        return await asyncio.to_thread(self._get_active, repository_id)

    def _get_active(self, repository_id: RepositoryId) -> RepositorySnapshot | None:
        with self._sessions() as session:
            row = session.scalar(
                select(GenerationRow).where(
                    GenerationRow.repository_id == repository_id.value,
                    GenerationRow.status == IndexGenerationStatus.ACTIVE.value,
                )
            )
            return self._snapshot(session, row) if row else None

    async def get_generation(self, generation_id: IndexGenerationId) -> RepositorySnapshot:
        return await asyncio.to_thread(self._get_generation, generation_id)

    def _get_generation(self, generation_id: IndexGenerationId) -> RepositorySnapshot:
        with self._sessions() as session:
            row = session.get(GenerationRow, generation_id.value)
            if row is None:
                raise NotFoundError("The index generation was not found.")
            return self._snapshot(session, row)

    async def get_status(self, generation_id: IndexGenerationId) -> IndexGenerationStatus:
        return await asyncio.to_thread(self._get_status, generation_id)

    def _get_status(self, generation_id: IndexGenerationId) -> IndexGenerationStatus:
        with self._sessions() as session:
            row = session.get(GenerationRow, generation_id.value)
            if row is None:
                raise NotFoundError("The index generation was not found.")
            return IndexGenerationStatus(row.status)

    async def list_generations(self, repository_id: RepositoryId) -> tuple[RepositorySnapshot, ...]:
        return await asyncio.to_thread(self._list_generations, repository_id)

    def _list_generations(self, repository_id: RepositoryId) -> tuple[RepositorySnapshot, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(GenerationRow)
                .where(GenerationRow.repository_id == repository_id.value)
                .order_by(GenerationRow.generation_number)
            ).all()
            return tuple(self._snapshot(session, row) for row in rows)

    @staticmethod
    def _snapshot(session: Session, row: GenerationRow) -> RepositorySnapshot:
        snapshot = session.scalar(
            select(SnapshotRow).where(SnapshotRow.generation_id == row.generation_id)
        )
        if snapshot is None:
            raise IndexValidationError("Generation snapshot metadata is missing.")
        return RepositorySnapshot(
            RepositoryId(row.repository_id),
            row.commit_sha,
            IndexGenerationId(row.generation_id),
            row.generation_number,
            snapshot.parser_fingerprint,
            snapshot.policy_fingerprint,
            tuple(json.loads(snapshot.parser_metadata_json)),
        )

    async def save_validation(
        self, generation_id: IndexGenerationId, valid: bool, details: tuple[str, ...]
    ) -> None:
        await asyncio.to_thread(self._save_validation, generation_id, valid, details)

    def _save_validation(
        self, generation_id: IndexGenerationId, valid: bool, details: tuple[str, ...]
    ) -> None:
        with self._sessions.begin() as session:
            session.merge(
                ValidationResultRow(
                    generation_id=generation_id.value,
                    valid=valid,
                    details_json=json.dumps(details),
                    validated_at=_now(),
                )
            )

    async def validate_generation(
        self, generation_id: IndexGenerationId, maximum_failure_rate: float
    ) -> tuple[bool, tuple[str, ...]]:
        return await asyncio.to_thread(
            self._validate_generation, generation_id, maximum_failure_rate
        )

    def _validate_generation(
        self, generation_id: IndexGenerationId, maximum_failure_rate: float
    ) -> tuple[bool, tuple[str, ...]]:
        details: list[str] = []
        with self._sessions() as session:
            generation = session.get(GenerationRow, generation_id.value)
            if generation is None or generation.status != IndexGenerationStatus.VALIDATING.value:
                details.append("generation status is not validating")
                return False, tuple(details)
            files = session.scalars(
                select(IndexedFileRow).where(IndexedFileRow.generation_id == generation_id.value)
            ).all()
            failed = sum(row.security_status == ContentSecurityStatus.FAILED.value for row in files)
            if files and failed / len(files) > maximum_failure_rate:
                details.append("file failure rate exceeds policy")
            for row in files:
                version = session.get(FileVersionRow, row.row_id)
                if version is None or version.commit_sha != generation.commit_sha:
                    details.append("file version does not match generation commit")
                    break
                if row.security_status == ContentSecurityStatus.SAFE.value:
                    if version.content_utf8 is None:
                        details.append("safe file content is missing")
                        break
                elif version.content_utf8 is not None:
                    details.append("unsafe file content was persisted")
                    break
            symbols = session.scalars(
                select(SymbolRow).where(SymbolRow.generation_id == generation_id.value)
            ).all()
            file_sizes = {row.row_id: row.byte_size for row in files}
            symbol_ids = {row.symbol_id for row in symbols}
            for symbol in symbols:
                if symbol.file_row_id not in file_sizes or not (
                    0 <= symbol.start_byte <= symbol.end_byte <= file_sizes[symbol.file_row_id]
                ):
                    details.append("symbol source span is outside its file")
                    break
                if symbol.parent_symbol_id and symbol.parent_symbol_id not in symbol_ids:
                    details.append("symbol parent is outside the generation")
                    break
                if not symbol.parser_id or not symbol.parser_version or not symbol.grammar_version:
                    details.append("symbol parser metadata is missing")
                    break
            for row_type in (ImportRelationRow, ExportRelationRow, ReferenceRelationRow):
                relations = session.scalars(
                    select(row_type).where(row_type.generation_id == generation_id.value)
                ).all()
                if any(
                    cast(RelationMixin, relation).file_row_id not in file_sizes
                    or (
                        cast(RelationMixin, relation).source_symbol_id is not None
                        and cast(RelationMixin, relation).source_symbol_id not in symbol_ids
                    )
                    for relation in relations
                ):
                    details.append("relation endpoint is outside the generation")
                    break
        return not details, tuple(details or ["validation passed"])

    async def activate(self, generation_id: IndexGenerationId) -> None:
        await asyncio.to_thread(self._activate, generation_id)

    def _activate(self, generation_id: IndexGenerationId) -> None:
        with self._sessions.begin() as session:
            target = session.get(GenerationRow, generation_id.value)
            validation = session.get(ValidationResultRow, generation_id.value)
            if target is None or validation is None or not validation.valid:
                raise IndexValidationError()
            validate_generation_transition(
                IndexGenerationStatus(target.status), IndexGenerationStatus.ACTIVE
            )
            session.execute(
                update(GenerationRow)
                .where(
                    GenerationRow.repository_id == target.repository_id,
                    GenerationRow.status == IndexGenerationStatus.ACTIVE.value,
                )
                .values(status=IndexGenerationStatus.SUPERSEDED.value, updated_at=_now())
            )
            target.status = IndexGenerationStatus.ACTIVE.value
            target.updated_at = _now()

    async def fail_generation(
        self,
        generation_id: IndexGenerationId,
        failure: IndexingFailure,
        statistics: IndexingStatistics,
    ) -> None:
        await asyncio.to_thread(self._fail_generation, generation_id, failure, statistics)

    def _fail_generation(
        self,
        generation_id: IndexGenerationId,
        failure: IndexingFailure,
        statistics: IndexingStatistics,
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(GenerationRow, generation_id.value)
            if row is None or row.status in {
                IndexGenerationStatus.ACTIVE.value,
                IndexGenerationStatus.SUPERSEDED.value,
            }:
                return
            row.status = IndexGenerationStatus.FAILED.value
            row.failure_code = failure.code[:64]
            row.failure_message = failure.safe_message[:256]
            row.statistics_json = json.dumps(asdict(statistics))
            row.updated_at = _now()
            delete_generation_fts(session, generation_id.value)

    async def save_statistics(
        self, generation_id: IndexGenerationId, statistics: IndexingStatistics
    ) -> None:
        await asyncio.to_thread(self._save_statistics, generation_id, statistics)

    def _save_statistics(
        self, generation_id: IndexGenerationId, statistics: IndexingStatistics
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(GenerationRow, generation_id.value)
            if row is None:
                raise NotFoundError("The index generation was not found.")
            row.statistics_json = json.dumps(asdict(statistics))
            row.updated_at = _now()

    async def mark_stale_failed(self, stale_before: datetime) -> int:
        return await asyncio.to_thread(self._mark_stale_failed, stale_before)

    def _mark_stale_failed(self, stale_before: datetime) -> int:
        cutoff = stale_before.astimezone(UTC).isoformat()
        with self._sessions.begin() as session:
            jobs = session.scalars(
                select(IndexJobRow).where(IndexJobRow.lease_expires_at < cutoff)
            ).all()
            for job in jobs:
                session.execute(
                    update(GenerationRow)
                    .where(
                        GenerationRow.repository_id == job.repository_id,
                        GenerationRow.owner_token == job.owner_token,
                        GenerationRow.status.in_(
                            [
                                IndexGenerationStatus.PENDING.value,
                                IndexGenerationStatus.RUNNING.value,
                                IndexGenerationStatus.VALIDATING.value,
                            ]
                        ),
                    )
                    .values(
                        status=IndexGenerationStatus.FAILED.value,
                        failure_code="stale_job",
                        failure_message="The indexing lease expired.",
                        updated_at=_now(),
                    )
                )
                session.delete(job)
            return len(jobs)

    async def rollback(self, repository_id: RepositoryId, generation_id: IndexGenerationId) -> None:
        await asyncio.to_thread(self._rollback, repository_id, generation_id)

    def _rollback(self, repository_id: RepositoryId, generation_id: IndexGenerationId) -> None:
        with self._sessions.begin() as session:
            target = session.get(GenerationRow, generation_id.value)
            if (
                target is None
                or target.repository_id != repository_id.value
                or target.status != IndexGenerationStatus.SUPERSEDED.value
            ):
                raise IndexValidationError("Only a superseded generation can be restored.")
            session.execute(
                update(GenerationRow)
                .where(
                    GenerationRow.repository_id == repository_id.value,
                    GenerationRow.status == IndexGenerationStatus.ACTIVE.value,
                )
                .values(status=IndexGenerationStatus.SUPERSEDED.value, updated_at=_now())
            )
            target.status = IndexGenerationStatus.ACTIVE.value
            target.updated_at = _now()
