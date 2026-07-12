"""Bounded TypeScript and JavaScript parser tests."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.domain.errors import LimitExceededError
from codeatlas.domain.indexing_models import (
    ContentSecurityStatus,
    FileVersion,
    IndexedFile,
    IndexedFileId,
    IndexGenerationId,
    RepositorySnapshot,
    SymbolKind,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath


def parse_source(source: str, path: str = "src/example.ts") -> object:
    """Parse bounded source with deterministic fixture identities."""
    repository_id = RepositoryId("repo_" + "1" * 32)
    generation_id = IndexGenerationId("gen_" + "2" * 32)
    repository_path = RepositoryPath(PurePosixPath(path))
    file_id = IndexedFileId.derive(repository_id, repository_path)
    snapshot = RepositorySnapshot(
        repository_id,
        "a" * 40,
        generation_id,
        1,
        "parser",
        "policy",
    )
    file = IndexedFile(
        file_id,
        generation_id,
        repository_path,
        FileVersion(file_id, "a" * 40, "b" * 40, "c" * 64),
        len(source.encode()),
        len(source.splitlines()),
        "typescript",
        "utf-8",
        ContentSecurityStatus.SAFE,
        None,
        source,
    )
    return TreeSitterTypeScriptParser().parse(snapshot, file)


def test_typescript_declarations_relations_and_nested_symbols() -> None:
    """Core TS declarations and unresolved graph candidates retain parser provenance."""
    parsed = parse_source(
        """import { Base } from './base';
/** docs */ export interface Shape { area(): number }
export class Box extends Base implements Shape {
  constructor(public size: number) {}
  area(): number { return helper(this.size); }
}
export type Size = number;
export enum Mode { A }
export function helper(value: number) { return value; }
const callback = (x: number) => x + 1, second = 2;
namespace Internal { export const value = 1; }
"""
    )
    kinds = {symbol.kind for symbol in parsed.symbols}  # type: ignore[union-attr]
    assert {
        SymbolKind.INTERFACE,
        SymbolKind.CLASS,
        SymbolKind.CONSTRUCTOR,
        SymbolKind.METHOD,
        SymbolKind.TYPE_ALIAS,
        SymbolKind.ENUM,
        SymbolKind.FUNCTION,
        SymbolKind.VARIABLE,
        SymbolKind.ANONYMOUS_FUNCTION,
        SymbolKind.NAMESPACE,
    } <= kinds
    assert parsed.imports and parsed.exports and parsed.references  # type: ignore[union-attr]
    assert {relation.relation_kind for relation in parsed.references} >= {  # type: ignore[union-attr]
        "call",
        "class_heritage",
    }
    assert {symbol.name for symbol in parsed.symbols} >= {  # type: ignore[union-attr]
        "callback",
        "second",
    }
    assert all(symbol.parser_version for symbol in parsed.symbols)  # type: ignore[union-attr]
    method = next(
        symbol
        for symbol in parsed.symbols
        if symbol.kind is SymbolKind.METHOD  # type: ignore[union-attr]
    )
    assert method.parent_symbol_id is not None


def test_javascript_unicode_crlf_and_syntax_errors() -> None:
    """Unicode columns and CRLF lines remain stable while syntax errors are recorded."""
    parsed = parse_source(
        "export class 日本語 {\r\n  method() { return (() => 1)(); }\r\n}\r\nfunction broken( {",
        "src/example.js",
    )
    names = {symbol.name for symbol in parsed.symbols}  # type: ignore[union-attr]
    assert "日本語" in names
    assert parsed.syntax_error_count > 0  # type: ignore[union-attr]
    assert all(symbol.span.start_line >= 1 for symbol in parsed.symbols)  # type: ignore[union-attr]


def test_parser_rejects_oversized_input() -> None:
    """Source bytes are bounded before Tree-sitter allocates a large AST."""
    parser = TreeSitterTypeScriptParser(max_source_bytes=16)
    repository_id = RepositoryId("repo_" + "1" * 32)
    generation_id = IndexGenerationId("gen_" + "2" * 32)
    path = RepositoryPath(PurePosixPath("large.ts"))
    file_id = IndexedFileId.derive(repository_id, path)
    snapshot = RepositorySnapshot(repository_id, "a" * 40, generation_id, 1, "p", "q")
    file = IndexedFile(
        file_id,
        generation_id,
        path,
        FileVersion(file_id, "a" * 40, "b" * 40, "c" * 64),
        100,
        1,
        "typescript",
        "utf-8",
        ContentSecurityStatus.SAFE,
        None,
        "const value = 'this is too large';",
    )
    with pytest.raises(LimitExceededError):
        parser.parse(snapshot, file)
