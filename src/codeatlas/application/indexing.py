"""Application orchestration for transactional full and incremental indexing."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from codeatlas.domain.errors import (
    BinaryFileError,
    CodeAtlasError,
    IndexBusyError,
    IndexingError,
    IndexValidationError,
    LimitExceededError,
    RepositoryError,
    SecretDetectedError,
    SecurityError,
)
from codeatlas.domain.indexing_models import (
    ContentSecurityStatus,
    FileVersion,
    IndexedFile,
    IndexedFileId,
    IndexGenerationId,
    IndexGenerationStatus,
    IndexingFailure,
    IndexingRequest,
    IndexingResult,
    IndexingStatistics,
    ParsedFile,
    RepositorySnapshot,
)
from codeatlas.domain.indexing_ports import IndexStorePort, SourceParserPort
from codeatlas.domain.repository_models import (
    RepositoryEntry,
    RepositoryEntryType,
    RepositoryId,
    RepositoryPath,
    ResolvedRevision,
    RevisionRef,
)
from codeatlas.domain.repository_ports import RepositoryProviderPort


@dataclass(frozen=True, slots=True)
class IndexingPolicy:
    """Application-level M3 indexing limits and deterministic exclusions."""

    max_repository_files: int = 100_000
    max_concurrency: int = 4
    operation_timeout_seconds: float = 300.0
    lease_timeout_seconds: float = 120.0
    lock_wait_timeout_seconds: float = 5.0
    max_failure_rate: float = 0.05
    exclude_generated: bool = True
    exclude_minified: bool = True

    @property
    def fingerprint(self) -> str:
        """Return a stable policy identity used for incremental invalidation."""
        return hashlib.sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    file: IndexedFile
    parsed: ParsedFile | None
    reason: str | None = None


class TransactionalIndexingService:
    """Build, validate, and atomically publish immutable SQLite generations."""

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self,
        repository: RepositoryProviderPort,
        store: IndexStorePort,
        parsers: tuple[SourceParserPort, ...],
        policy: IndexingPolicy,
    ) -> None:
        self.repository = repository
        self.store = store
        self.parsers = tuple(sorted(parsers, key=lambda parser: parser.parser_id))
        self.policy = policy

    @property
    def parser_fingerprint(self) -> str:
        """Hash parser/grammar versions to invalidate incompatible reuse."""
        values = (
            f"{parser.parser_id}:{parser.parser_version}:{parser.grammar_version}"
            for parser in self.parsers
        )
        return hashlib.sha256("\n".join(values).encode()).hexdigest()

    @property
    def parser_metadata(self) -> tuple[str, ...]:
        """Return readable parser and grammar versions stored with each snapshot."""
        return tuple(
            f"{parser.parser_id}:{parser.parser_version}:{parser.grammar_version}"
            for parser in self.parsers
        )

    async def create_index(self, request: IndexingRequest) -> IndexingResult:
        """Create a full or incremental generation and publish it after validation."""
        await self.store.assert_schema_current()
        lock = self._locks.setdefault(request.repository_id.value, asyncio.Lock())
        try:
            async with asyncio.timeout(self.policy.lock_wait_timeout_seconds):
                await lock.acquire()
        except TimeoutError as error:
            raise IndexBusyError(internal_cause=error) from error
        owner = uuid.uuid4().hex
        generation: RepositorySnapshot | None = None
        statistics = IndexingStatistics()
        started = datetime.now(UTC)
        try:
            resolved = await self.repository.resolve_revision(
                request.repository_id, RevisionRef(request.revision)
            )
            await self.store.acquire_lease(
                request.repository_id,
                owner,
                datetime.now(UTC)
                + timedelta(
                    seconds=(
                        self.policy.operation_timeout_seconds + self.policy.lease_timeout_seconds
                    )
                ),
            )
            generation = await self.store.create_generation(
                request.repository_id,
                resolved.commit_sha,
                self.parser_fingerprint,
                self.policy.fingerprint,
                owner,
                self.parser_metadata,
            )
            await self.store.set_status(generation.generation_id, IndexGenerationStatus.RUNNING)
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                statistics = await self._build(generation, resolved, request.incremental)
                await self.store.save_statistics(generation.generation_id, statistics)
                await self.store.set_status(
                    generation.generation_id, IndexGenerationStatus.VALIDATING
                )
                valid, details = await self.store.validate_generation(
                    generation.generation_id, self.policy.max_failure_rate
                )
                await self.store.save_validation(generation.generation_id, valid, details)
                if not valid:
                    raise IndexValidationError()
                await self.store.activate(generation.generation_id)
            return IndexingResult(
                generation,
                IndexGenerationStatus.ACTIVE,
                statistics,
                started,
                datetime.now(UTC),
            )
        except asyncio.CancelledError:
            if generation is not None:
                await asyncio.shield(
                    self.store.fail_generation(
                        generation.generation_id,
                        IndexingFailure("cancelled", "Indexing was cancelled."),
                        statistics,
                    )
                )
            raise
        except Exception as error:
            if generation is not None:
                safe = str(error) if isinstance(error, CodeAtlasError) else "Indexing failed."
                await self.store.fail_generation(
                    generation.generation_id,
                    IndexingFailure("indexing_failed", safe),
                    statistics,
                )
            if isinstance(error, CodeAtlasError):
                raise
            raise IndexingError(internal_cause=error) from error
        finally:
            try:
                await self.store.release_lease(request.repository_id, owner)
            finally:
                lock.release()

    async def _build(
        self,
        snapshot: RepositorySnapshot,
        revision: ResolvedRevision,
        incremental: bool,
    ) -> IndexingStatistics:
        entries = await self._list_files(revision)
        previous = await self.store.get_active(snapshot.repository_id) if incremental else None
        previous_files = (
            {
                item.path.as_posix(): item
                for item in await self.store.list_files(previous.generation_id)
            }
            if previous is not None
            else {}
        )
        reasons: Counter[str] = Counter()
        indexed = parsed = reused = skipped = failed = symbols = relations = 0
        for offset in range(0, len(entries), self.policy.max_concurrency):
            chunk = entries[offset : offset + self.policy.max_concurrency]
            prepared = await asyncio.gather(
                *(
                    self._prepare_or_reuse(snapshot, revision, entry, previous, previous_files)
                    for entry in chunk
                )
            )
            for item in prepared:
                if isinstance(item, tuple):
                    source_generation, path = item
                    if await self.store.copy_file_bundle(
                        source_generation,
                        snapshot.generation_id,
                        path,
                        snapshot.commit_sha,
                    ):
                        indexed += 1
                        reused += 1
                        continue
                assert isinstance(item, _PreparedFile)
                parsed_file = item.parsed
                await self.store.add_file_bundle(
                    item.file,
                    parsed_file.symbols if parsed_file else (),
                    parsed_file.imports if parsed_file else (),
                    parsed_file.exports if parsed_file else (),
                    parsed_file.references if parsed_file else (),
                )
                if item.file.security_status is ContentSecurityStatus.SAFE:
                    indexed += 1
                elif item.file.security_status is ContentSecurityStatus.SKIPPED:
                    skipped += 1
                else:
                    failed += 1
                if item.reason:
                    reasons[item.reason] += 1
                if parsed_file:
                    parsed += 1
                    symbols += len(parsed_file.symbols)
                    relations += (
                        len(parsed_file.imports)
                        + len(parsed_file.exports)
                        + len(parsed_file.references)
                    )
        return IndexingStatistics(
            discovered=len(entries),
            indexed=indexed,
            parsed=parsed,
            reused=reused,
            skipped=skipped,
            failed=failed,
            symbols=symbols,
            relations=relations,
            skip_reasons=tuple(sorted(reasons.items())),
        )

    async def _prepare_or_reuse(
        self,
        snapshot: RepositorySnapshot,
        revision: ResolvedRevision,
        entry: RepositoryEntry,
        previous: RepositorySnapshot | None,
        previous_files: dict[str, IndexedFile],
    ) -> _PreparedFile | tuple[IndexGenerationId, RepositoryPath]:
        old = previous_files.get(entry.path.as_posix())
        if (
            previous is not None
            and old is not None
            and old.version.git_object_id == entry.object_id
            and previous.parser_fingerprint == snapshot.parser_fingerprint
            and previous.policy_fingerprint == snapshot.policy_fingerprint
        ):
            return previous.generation_id, entry.path
        return await self._prepare(snapshot, revision, entry)

    async def _prepare(
        self,
        snapshot: RepositorySnapshot,
        revision: ResolvedRevision,
        entry: RepositoryEntry,
    ) -> _PreparedFile:
        file_id = IndexedFileId.derive(snapshot.repository_id, entry.path)
        if entry.entry_type is not RepositoryEntryType.FILE:
            return self._skipped(snapshot, entry, file_id, entry.entry_type.value)
        parser = next(
            (candidate for candidate in self.parsers if candidate.supports(entry.path)), None
        )
        if self._excluded_generated(entry.path):
            return self._skipped(snapshot, entry, file_id, "generated")
        try:
            content = await self.repository.read_file(revision, entry.path)
        except (SecretDetectedError, BinaryFileError, LimitExceededError, SecurityError) as error:
            return self._skipped(snapshot, entry, file_id, error.code)
        except RepositoryError:
            return self._failed(snapshot, entry, file_id, "repository_read_failed")
        indexed = IndexedFile(
            file_id,
            snapshot.generation_id,
            entry.path,
            FileVersion(file_id, snapshot.commit_sha, entry.object_id, content.content_hash),
            content.byte_size,
            content.line_count,
            self._language(entry.path),
            content.encoding,
            ContentSecurityStatus.SAFE,
            None,
            content.content,
            parser.parser_id if parser else None,
            parser.parser_version if parser else None,
            parser.grammar_version if parser else None,
        )
        if parser is None:
            return _PreparedFile(indexed, None)
        try:
            parsed = await asyncio.to_thread(parser.parse, snapshot, indexed)
        except (IndexingError, LimitExceededError):
            return _PreparedFile(
                replace(
                    indexed,
                    security_status=ContentSecurityStatus.FAILED,
                    status_reason="parser_failed",
                    content=None,
                ),
                None,
                "parser_failed",
            )
        return _PreparedFile(replace(indexed, syntax_error_count=parsed.syntax_error_count), parsed)

    async def _list_files(self, revision: ResolvedRevision) -> tuple[RepositoryEntry, ...]:
        files: list[RepositoryEntry] = []
        pending = [RepositoryPath.root()]
        while pending:
            directory = pending.pop()
            for entry in await self.repository.list_directory(revision, directory):
                if entry.entry_type is RepositoryEntryType.DIRECTORY:
                    pending.append(entry.path)
                elif entry.entry_type in {
                    RepositoryEntryType.FILE,
                    RepositoryEntryType.SYMLINK,
                    RepositoryEntryType.SUBMODULE,
                }:
                    files.append(entry)
                    if len(files) > self.policy.max_repository_files:
                        raise LimitExceededError("The repository has too many indexable files.")
        return tuple(sorted(files, key=lambda item: item.path.as_posix()))

    def _skipped(
        self,
        snapshot: RepositorySnapshot,
        entry: RepositoryEntry,
        file_id: IndexedFileId,
        reason: str,
    ) -> _PreparedFile:
        return _PreparedFile(
            IndexedFile(
                file_id,
                snapshot.generation_id,
                entry.path,
                FileVersion(file_id, snapshot.commit_sha, entry.object_id, ""),
                entry.size or 0,
                0,
                self._language(entry.path),
                None,
                ContentSecurityStatus.SKIPPED,
                reason,
                None,
            ),
            None,
            reason,
        )

    def _failed(
        self,
        snapshot: RepositorySnapshot,
        entry: RepositoryEntry,
        file_id: IndexedFileId,
        reason: str,
    ) -> _PreparedFile:
        skipped = self._skipped(snapshot, entry, file_id, reason)
        return replace(
            skipped,
            file=replace(skipped.file, security_status=ContentSecurityStatus.FAILED),
        )

    def _excluded_generated(self, path: RepositoryPath) -> bool:
        name = path.value.name.casefold()
        generated = self.policy.exclude_generated and (
            ".generated." in name or name.endswith(".gen.ts")
        )
        minified = self.policy.exclude_minified and name.endswith((".min.js", ".min.mjs"))
        return generated or minified

    @staticmethod
    def _language(path: RepositoryPath) -> str | None:
        return {
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".thrift": "thrift",
        }.get(path.value.suffix.casefold())

    async def get_active_generation(self, repository_id: RepositoryId) -> RepositorySnapshot | None:
        """Return the active generation for a typed repository ID."""
        return await self.store.get_active(repository_id)

    async def get_generation_status(
        self, generation_id: IndexGenerationId
    ) -> IndexGenerationStatus:
        """Return the persisted lifecycle status."""
        return await self.store.get_status(generation_id)

    async def list_generations(self, repository_id: RepositoryId) -> tuple[RepositorySnapshot, ...]:
        """List immutable generations in creation order."""
        return await self.store.list_generations(repository_id)

    async def validate_generation(
        self, generation_id: IndexGenerationId
    ) -> tuple[bool, tuple[str, ...]]:
        """Run and persist pre-activation validation."""
        valid, details = await self.store.validate_generation(
            generation_id, self.policy.max_failure_rate
        )
        await self.store.save_validation(generation_id, valid, details)
        return valid, details

    async def activate_generation(self, generation_id: IndexGenerationId) -> None:
        """Activate an already validated staging generation."""
        await self.store.activate(generation_id)

    async def mark_stale_jobs_failed(self, stale_before: datetime) -> int:
        """Fail expired staging jobs without touching active generations."""
        return await self.store.mark_stale_failed(stale_before)

    async def rollback_active(
        self, repository_id: RepositoryId, generation_id: IndexGenerationId
    ) -> None:
        """Atomically restore one superseded generation."""
        await self.store.rollback(repository_id, generation_id)
