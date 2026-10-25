"""End-to-end Thrift indexing and cross-language relation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GitRepositoryFixture, run_git

from codeatlas.adapters.parsing.thrift_parser import ThriftParser
from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.application.indexing import IndexingPolicy, TransactionalIndexingService
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.indexing_models import IndexingRequest, SymbolKind
from codeatlas.domain.repository_models import RepositoryLocation
from codeatlas.domain.retrieval_models import SymbolQuery
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.infrastructure.indexing.query import SqliteIndexQuery
from codeatlas.infrastructure.indexing.store import SqliteIndexStore

_THRIFT = """
namespace java com.example.line
struct User {
  1: required i64 id,
}
service LineService {
  User getUser(1: required i64 id),
}
"""
_TYPESCRIPT = "export class LineServiceClient { getUser(id: number) { return id; } }\n"


@pytest.mark.asyncio
async def test_thrift_symbols_are_indexed_and_link_to_typescript(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    root = git_repository.root
    (root / "line.thrift").write_text(_THRIFT, encoding="utf-8")
    (root / "client.ts").write_text(_TYPESCRIPT, encoding="utf-8")
    run_git(root, "add", "line.thrift", "client.ts")
    run_git(root, "commit", "-m", "add thrift fixtures")

    url = f"sqlite:///{(tmp_path / 'thrift.db').as_posix()}"
    upgrade_database(url)
    provider = LocalGitRepositoryPlugin.from_settings(RepositorySettings()).provider
    store = SqliteIndexStore(url)
    service = TransactionalIndexingService(
        provider,
        store,
        (TreeSitterTypeScriptParser(), ThriftParser()),
        IndexingPolicy(max_failure_rate=0.05),
    )
    descriptor = await service.repository.register_local_repository(RepositoryLocation(root))
    result = await service.create_index(
        IndexingRequest(descriptor.repository_id, "main", incremental=False)
    )
    generation_id = result.snapshot.generation_id.value
    query = SqliteIndexQuery(url)
    try:
        service_symbols = await query.find_symbols(
            generation_id,
            SymbolQuery(name="LineService", kinds=(SymbolKind.SERVICE,)),
        )
        assert service_symbols
        service_symbol = service_symbols[0]
        method_symbols = await query.find_symbols(
            generation_id,
            SymbolQuery(
                name="getUser",
                kinds=(SymbolKind.METHOD,),
                path_prefix="line.thrift",
            ),
        )
        assert method_symbols
        assert method_symbols[0].parent_symbol_id == service_symbol.symbol_id

        relations = await query.find_relations_filtered(
            generation_id,
            source_symbol_id=service_symbol.symbol_id,
            limit=20,
        )
        targets = {relation.raw_target for relation in relations}
        assert "LineServiceClient" in targets

        ts_symbols = await query.find_symbols(
            generation_id,
            SymbolQuery(name="LineServiceClient", kinds=(SymbolKind.CLASS,)),
        )
        assert ts_symbols
        assert ts_symbols[0].file_id != service_symbol.file_id
    finally:
        await query.aclose()
