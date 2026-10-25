"""Contract, limiting, and security tests for the SQLite index query adapter."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
from conftest import run_git
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.application.indexing import IndexingPolicy, TransactionalIndexingService
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.errors import IndexQueryError
from codeatlas.domain.indexing_models import (
    IndexGenerationStatus,
    IndexingRequest,
    SymbolId,
    SymbolKind,
)
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
    RepositoryPath,
)
from codeatlas.domain.retrieval_models import (
    IndexTextHit,
    RelationKind,
    RetrievalLimits,
    SymbolQuery,
)
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.infrastructure.indexing.query import SqliteIndexQuery
from codeatlas.infrastructure.indexing.store import SqliteIndexStore

_SERVICE_INITIAL = "export function helper() { return 1; }\n"
_SERVICE_FINAL = (
    'import { helper } from "./helper";\n'
    "export class Service extends Base {\n"
    "  run(): number { return helper(); }\n"
    "}\n"
    "export function helper() { return 1; }\n"
)
_OTHER_FINAL = 'import { Service } from "./service";\nexport const value = 1;\n'
_NOTES = "first line\nneedle second line\nthird line\n"
_LONG_LINE = f"{'x' * 200}needle{'y' * 200}\n"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class IndexedRepository:
    """A real temporary Git repository indexed into a migrated SQLite database."""

    root: Path
    descriptor: RepositoryDescriptor
    first_sha: str
    head_sha: str
    generation_id: str
    database_url: str
    query: SqliteIndexQuery
    store: SqliteIndexStore


@pytest.fixture
async def indexed_repository(tmp_path: Path) -> AsyncIterator[IndexedRepository]:
    """Index two commits containing TypeScript symbols, relations, and safe text."""
    root = tmp_path / "repository"
    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "CodeAtlas Test")
    run_git(root, "config", "user.email", "tests@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "service.ts").write_text(_SERVICE_INITIAL, encoding="utf-8")
    (root / "notes.txt").write_text(_NOTES, encoding="utf-8")
    (root / "long.txt").write_text(_LONG_LINE, encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial commit")
    first_sha = run_git(root, "rev-parse", "HEAD")
    run_git(root, "tag", "v1", first_sha)
    (root / "src" / "service.ts").write_text(_SERVICE_FINAL, encoding="utf-8")
    (root / "src" / "other.ts").write_text(_OTHER_FINAL, encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "add service API")
    head_sha = run_git(root, "rev-parse", "HEAD")
    (root / "src" / "service.ts").write_text("working tree only\n", encoding="utf-8")

    plugin = LocalGitRepositoryPlugin.from_settings(RepositorySettings())
    provider = plugin.provider
    database_url = f"sqlite:///{(tmp_path / 'index.db').as_posix()}"
    upgrade_database(database_url)
    store = SqliteIndexStore(database_url)
    service = TransactionalIndexingService(
        provider, store, (TreeSitterTypeScriptParser(),), IndexingPolicy()
    )
    descriptor = await provider.register_local_repository(RepositoryLocation(root))
    result = await service.create_index(IndexingRequest(descriptor.repository_id, "main"))
    assert result.snapshot.commit_sha == head_sha
    query = SqliteIndexQuery(database_url)
    try:
        yield IndexedRepository(
            root=root,
            descriptor=descriptor,
            first_sha=first_sha,
            head_sha=head_sha,
            generation_id=result.snapshot.generation_id.value,
            database_url=database_url,
            query=query,
            store=store,
        )
    finally:
        await query.aclose()
        store.engine.dispose()


async def test_active_snapshot_and_bounded_files(indexed_repository: IndexedRepository) -> None:
    """The active generation is reported and file metadata carries stored content."""
    repository = indexed_repository
    snapshot = await repository.query.get_active_snapshot(repository.descriptor.repository_id)
    assert snapshot is not None
    assert snapshot.commit_sha == repository.head_sha
    assert snapshot.generation_id == repository.generation_id
    assert snapshot.generation_number == 1
    assert snapshot.status is IndexGenerationStatus.ACTIVE
    unknown = RepositoryId("repo_" + "0" * 32)
    assert await repository.query.get_active_snapshot(unknown) is None

    files = await repository.query.list_files(repository.generation_id)
    by_path = {item.path.as_posix(): item for item in files}
    assert set(by_path) == {"long.txt", "notes.txt", "src/other.ts", "src/service.ts"}
    service_file = by_path["src/service.ts"]
    assert service_file.content is not None
    assert "class Service" in service_file.content
    assert "working tree only" not in service_file.content
    assert service_file.version.commit_sha == repository.head_sha
    assert _SHA_PATTERN.fullmatch(service_file.version.commit_sha) is not None

    fetched = await repository.query.get_file(
        repository.generation_id, RepositoryPath(PurePosixPath("src/service.ts"))
    )
    assert fetched is not None
    assert fetched.file_id == service_file.file_id
    missing = await repository.query.get_file(
        repository.generation_id, RepositoryPath(PurePosixPath("missing.ts"))
    )
    assert missing is None


async def test_find_symbols_exact_qualified_kind_path_and_limit(
    indexed_repository: IndexedRepository,
) -> None:
    """Exact, qualified-suffix, kind, path, and limit filters are all enforced."""
    repository = indexed_repository
    generation = repository.generation_id
    helpers = await repository.query.find_symbols(generation, SymbolQuery(name="helper"))
    assert len(helpers) == 1
    assert helpers[0].kind is SymbolKind.FUNCTION
    assert helpers[0].qualified_name == "helper"
    assert helpers[0].span.start_line == 5

    run_methods = await repository.query.find_symbols(
        generation, SymbolQuery(qualified_name="Service.run")
    )
    assert len(run_methods) == 1
    assert run_methods[0].kind is SymbolKind.METHOD
    assert run_methods[0].name == "run"

    suffix = await repository.query.find_symbols(generation, SymbolQuery(qualified_name="run"))
    assert [item.qualified_name for item in suffix] == ["Service.run"]

    classes = await repository.query.find_symbols(
        generation, SymbolQuery(name="Service", kinds=(SymbolKind.CLASS,))
    )
    assert len(classes) == 1
    assert classes[0].kind is SymbolKind.CLASS
    functions = await repository.query.find_symbols(
        generation, SymbolQuery(name="Service", kinds=(SymbolKind.FUNCTION,))
    )
    assert functions == ()

    scoped = await repository.query.find_symbols(
        generation, SymbolQuery(name="value", path_prefix="src/other.ts")
    )
    assert [item.name for item in scoped] == ["value"]
    wrong_prefix = await repository.query.find_symbols(
        generation, SymbolQuery(name="value", path_prefix="src/service.ts")
    )
    assert wrong_prefix == ()
    directory_prefix = await repository.query.find_symbols(
        generation, SymbolQuery(name="value", path_prefix="src")
    )
    assert [item.name for item in directory_prefix] == ["value"]

    limited = await repository.query.find_symbols(
        generation,
        SymbolQuery(
            kinds=(SymbolKind.CLASS, SymbolKind.FUNCTION, SymbolKind.METHOD),
            limit=2,
        ),
    )
    assert len(limited) == 2


async def test_list_symbols_get_symbol_and_roundtrip(
    indexed_repository: IndexedRepository,
) -> None:
    """File-scoped symbol listing and opaque identity lookup agree."""
    repository = indexed_repository
    generation = repository.generation_id
    service_path = RepositoryPath(PurePosixPath("src/service.ts"))
    symbols = await repository.query.list_symbols(generation, path=service_path, limit=50)
    assert {item.name for item in symbols} == {"Service", "run", "helper"}
    assert [item.span.start_byte for item in symbols] == sorted(
        item.span.start_byte for item in symbols
    )
    for symbol in symbols:
        fetched = await repository.query.get_symbol(generation, symbol.symbol_id)
        assert fetched == symbol
    unknown = SymbolId("symbol_" + "0" * 32)
    assert await repository.query.get_symbol(generation, unknown) is None
    everything = await repository.query.list_symbols(generation, path=None, limit=2)
    assert len(everything) == 2
    with pytest.raises(IndexQueryError) as raised:
        await repository.query.list_symbols(generation, path=None, limit=0)
    assert "SELECT" not in raised.value.safe_message


async def test_search_text_literal_line_columns_limits_and_case(
    indexed_repository: IndexedRepository,
) -> None:
    """Literal substring search returns accurate 1-based lines and 0-based columns."""
    repository = indexed_repository
    generation = repository.generation_id
    service_lines = _SERVICE_FINAL.splitlines()
    expected = [(1, match.start()) for match in re.finditer(re.escape("helper"), service_lines[0])]
    expected.append((3, service_lines[2].index("helper")))
    expected.append((5, service_lines[4].index("helper")))

    hits = await repository.query.search_text(
        generation, pattern="helper", case_sensitive=True, limit=10
    )
    service_hits = [hit for hit in hits if hit.path.as_posix() == "src/service.ts"]
    assert [(hit.line, hit.start_column) for hit in service_hits] == expected
    first = service_hits[0]
    assert first.excerpt == service_lines[0]
    assert first.end_column == first.start_column + len("helper")
    assert first.commit_sha == repository.head_sha

    insensitive = await repository.query.search_text(
        generation, pattern="HELPER", case_sensitive=False, limit=10
    )
    assert len(insensitive) == len(hits)
    sensitive_upper = await repository.query.search_text(
        generation, pattern="HELPER", case_sensitive=True, limit=10
    )
    assert sensitive_upper == ()

    literal = await repository.query.search_text(
        generation, pattern="return helper(", case_sensitive=True, limit=10
    )
    assert len(literal) == 1
    assert literal[0].line == 3
    regex_meta = await repository.query.search_text(
        generation, pattern=".*", case_sensitive=True, limit=10
    )
    assert regex_meta == ()
    assert isinstance(hits[0], IndexTextHit)

    bounded = await repository.query.search_text(
        generation, pattern="needle", case_sensitive=True, limit=2
    )
    assert len(bounded) == 2
    assert all(hit.excerpt.strip() for hit in bounded)
    long_query = SqliteIndexQuery(
        repository.database_url, limits=RetrievalLimits(max_excerpt_chars=80)
    )
    try:
        long_hits = await long_query.search_text(
            generation, pattern="needle", case_sensitive=True, limit=10
        )
    finally:
        await long_query.aclose()
    long_hit = next(hit for hit in long_hits if hit.path.as_posix() == "long.txt")
    assert len(long_hit.excerpt) <= 80
    assert "needle" in long_hit.excerpt


async def test_find_relations_by_kind_target_source_and_path(
    indexed_repository: IndexedRepository,
) -> None:
    """Relation filters map stored parser kinds to the domain relation vocabulary."""
    repository = indexed_repository
    generation = repository.generation_id
    imports = await repository.query.find_relations(
        generation,
        relation_kind=RelationKind.IMPORT,
        raw_target=None,
        source_symbol_id=None,
        limit=10,
    )
    import_targets = {(item.path.as_posix(), item.raw_target) for item in imports}
    assert ("src/service.ts", '"./helper"') in import_targets
    assert ("src/other.ts", '"./service"') in import_targets
    assert all(item.relation_kind is RelationKind.IMPORT for item in imports)

    calls = await repository.query.find_relations(
        generation,
        relation_kind=RelationKind.CALL,
        raw_target="helper",
        source_symbol_id=None,
        limit=10,
    )
    assert len(calls) == 1
    assert calls[0].path.as_posix() == "src/service.ts"
    assert calls[0].raw_target == "helper"
    assert calls[0].span.start_line == 3

    extends = await repository.query.find_relations(
        generation,
        relation_kind=RelationKind.EXTENDS,
        raw_target=None,
        source_symbol_id=None,
        limit=10,
    )
    assert extends
    assert all("Base" in item.raw_target for item in extends)

    run_symbols = await repository.query.find_symbols(
        generation, SymbolQuery(qualified_name="Service.run")
    )
    run_symbol = run_symbols[0]
    outgoing = await repository.query.find_relations(
        generation,
        relation_kind=None,
        raw_target=None,
        source_symbol_id=run_symbol.symbol_id,
        limit=10,
    )
    assert [item.raw_target for item in outgoing] == ["helper"]

    file_scope = await repository.query.find_relations_filtered(
        generation,
        relation_kinds=(RelationKind.IMPORT,),
        path=RepositoryPath(PurePosixPath("src/service.ts")),
        limit=10,
    )
    assert [item.raw_target for item in file_scope] == ['"./helper"']
    directory_scope = await repository.query.find_relations_filtered(
        generation,
        relation_kinds=(RelationKind.IMPORT,),
        path=RepositoryPath(PurePosixPath("src")),
        limit=10,
    )
    assert len(directory_scope) == 2
    limited = await repository.query.find_relations(
        generation,
        relation_kind=None,
        raw_target=None,
        source_symbol_id=None,
        limit=1,
    )
    assert len(limited) == 1


async def test_fts_lexical_search_is_ranked_and_path_filtered(
    indexed_repository: IndexedRepository,
) -> None:
    """FTS5 returns BM25-ranked line hits and honors a path prefix."""
    hits = await indexed_repository.query.search_lexical(
        indexed_repository.generation_id,
        query="needle",
        limit=5,
    )
    assert hits
    assert all(hit.score >= 0.0 for hit in hits)
    assert all(hit.commit_sha == indexed_repository.head_sha for hit in hits)
    needle_hits = [hit for hit in hits if hit.path.as_posix() == "notes.txt"]
    assert needle_hits
    assert needle_hits[0].line == 2
    assert "needle" in needle_hits[0].excerpt

    filtered = await indexed_repository.query.search_lexical(
        indexed_repository.generation_id,
        query="needle",
        limit=5,
        path_prefix="src/",
    )
    assert all(hit.path.as_posix().startswith("src/") for hit in filtered)


async def test_fts_lexical_search_rejects_empty_query(
    indexed_repository: IndexedRepository,
) -> None:
    """An empty lexical query is a safe error, not an empty match-all scan."""
    with pytest.raises(IndexQueryError):
        await indexed_repository.query.search_lexical(
            indexed_repository.generation_id,
            query="   ",
            limit=5,
        )


async def test_file_path_map_matches_file_records(
    indexed_repository: IndexedRepository,
) -> None:
    """The additive path map agrees with generation file records."""
    repository = indexed_repository
    files = await repository.query.list_files(repository.generation_id)
    paths = await repository.query.list_file_paths(repository.generation_id)
    assert {item.file_id for item in files} == set(paths)
    assert {paths[item.file_id].as_posix() for item in files} == {
        item.path.as_posix() for item in files
    }
    sample = files[0]
    subset = await repository.query.list_file_paths(
        repository.generation_id, file_ids=(sample.file_id,)
    )
    assert subset == {sample.file_id: sample.path}
    assert await repository.query.list_file_paths(repository.generation_id, file_ids=()) == {}


async def test_invalid_limits_and_generation_identifiers_are_safe(
    indexed_repository: IndexedRepository,
) -> None:
    """Invalid limits and generation IDs raise a safe IndexQueryError."""
    repository = indexed_repository
    generation = repository.generation_id
    with pytest.raises(IndexQueryError):
        await repository.query.search_text(generation, pattern="", case_sensitive=True, limit=10)
    with pytest.raises(IndexQueryError):
        await repository.query.search_text(
            generation, pattern="needle", case_sensitive=True, limit=0
        )
    with pytest.raises(IndexQueryError):
        await repository.query.find_relations(
            generation,
            relation_kind=None,
            raw_target=None,
            source_symbol_id=None,
            limit=0,
        )
    with pytest.raises(IndexQueryError):
        await repository.query.list_files("not-a-generation")
    with pytest.raises(IndexQueryError):
        await repository.query.find_symbols("not-a-generation", SymbolQuery(name="helper"))


async def test_unavailable_database_reports_safe_error(tmp_path: Path) -> None:
    """A missing database file becomes a safe error without SQL or paths."""
    database_url = f"sqlite:///{(tmp_path / 'absent' / 'index.db').as_posix()}"
    query = SqliteIndexQuery(database_url)
    try:
        with pytest.raises(IndexQueryError) as raised:
            await query.get_active_snapshot(RepositoryId("repo_" + "1" * 32))
        message = raised.value.safe_message
        assert "index.db" not in message
        assert "SELECT" not in message
        assert "sqlite" not in message.lower()
        assert "Traceback" not in message
    finally:
        await query.aclose()


async def test_query_connection_is_read_only(indexed_repository: IndexedRepository) -> None:
    """The adapter connection cannot mutate the index database."""
    repository = indexed_repository
    with repository.query._engine.connect() as connection:  # type: ignore[attr-defined]
        with pytest.raises(SQLAlchemyError):
            connection.execute(text("DELETE FROM symbols"))
