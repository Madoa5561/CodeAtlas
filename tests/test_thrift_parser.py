"""Thrift parser contract and cross-language relation tests."""

from __future__ import annotations

from pathlib import PurePosixPath

from codeatlas.adapters.parsing.thrift_parser import ThriftParser
from codeatlas.domain.indexing_models import (
    ContentSecurityStatus,
    FileVersion,
    IndexedFile,
    IndexedFileId,
    IndexGenerationId,
    RepositoryId,
    RepositorySnapshot,
    SymbolKind,
)
from codeatlas.domain.repository_models import RepositoryPath

_SHA = "a" * 40
_REPO = RepositoryId("repo_" + "b" * 32)
_GENERATION = IndexGenerationId("gen_" + "c" * 32)
_PATH = RepositoryPath(PurePosixPath("thrift/line.thrift"))

_THRIFT = """
namespace java com.example.line
include "shared/common.thrift"

typedef i64 UserId

const string DEFAULT_NAME = "guest"

enum Status {
  ACTIVE = 1,
  INACTIVE
}

struct User {
  1: required UserId id,
  2: optional string name,
  3: list<string> tags,
}

union Payload {
  1: string text,
  2: binary data,
}

exception NotFound {
  1: string message,
}

service LineService extends BaseService {
  User getUser(1: required UserId id) throws (1: NotFound error),
  oneway void ping(),
}
"""


def make_file(content: str = _THRIFT) -> IndexedFile:
    return IndexedFile(
        file_id=IndexedFileId.derive(_REPO, _PATH),
        generation_id=_GENERATION,
        path=_PATH,
        version=FileVersion(
            file_id=IndexedFileId.derive(_REPO, _PATH),
            commit_sha=_SHA,
            git_object_id="1" * 40,
            content_hash="2" * 64,
        ),
        byte_size=len(content.encode()),
        line_count=len(content.splitlines()),
        language="thrift",
        encoding="utf-8",
        security_status=ContentSecurityStatus.SAFE,
        status_reason=None,
        content=content,
    )


def snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_id=_REPO,
        commit_sha=_SHA,
        generation_id=_GENERATION,
        generation_number=1,
        parser_fingerprint="d" * 64,
        policy_fingerprint="e" * 64,
    )


def parse(content: str = _THRIFT):
    return ThriftParser().parse(snapshot(), make_file(content))


def test_thrift_parser_extracts_required_declarations() -> None:
    parsed = parse()
    kinds = {symbol.kind for symbol in parsed.symbols}
    names = {symbol.name for symbol in parsed.symbols}
    assert parsed.syntax_error_count == 0
    assert {
        SymbolKind.NAMESPACE,
        SymbolKind.TYPEDEF,
        SymbolKind.CONST,
        SymbolKind.ENUM,
        SymbolKind.STRUCT,
        SymbolKind.UNION,
        SymbolKind.EXCEPTION,
        SymbolKind.SERVICE,
        SymbolKind.METHOD,
        SymbolKind.FIELD,
    } <= kinds
    assert {"User", "Payload", "NotFound", "LineService", "getUser", "id", "name"} <= names
    service = next(symbol for symbol in parsed.symbols if symbol.kind is SymbolKind.SERVICE)
    method = next(symbol for symbol in parsed.symbols if symbol.name == "getUser")
    assert method.parent_symbol_id == service.symbol_id
    assert "throws" in method.signature
    user_id = next(symbol for symbol in parsed.symbols if symbol.qualified_name == "User.id")
    assert "required" in user_id.signature
    assert user_id.signature.startswith("1:")


def test_thrift_parser_emits_cross_language_and_type_relations() -> None:
    parsed = parse()
    targets = {relation.raw_target for relation in parsed.references}
    assert "LineServiceClient" in targets
    assert "BaseService" in targets
    assert "User" in targets
    assert "NotFound" in targets
    heuristic = next(
        relation for relation in parsed.references if relation.raw_target == "LineServiceClient"
    )
    assert heuristic.confidence == 0.5
    assert heuristic.resolver_metadata == "thrift-generated-client-heuristic"
    assert [relation.raw_target for relation in parsed.imports] == ["shared/common.thrift"]


def test_thrift_parser_supports_only_thrift_paths() -> None:
    parser = ThriftParser()
    assert parser.supports(RepositoryPath(PurePosixPath("a.thrift")))
    assert not parser.supports(RepositoryPath(PurePosixPath("a.ts")))


def test_thrift_parser_reports_malformed_input_safely() -> None:
    parsed = parse("service Broken { User getUser(1: UserId }")
    assert parsed.syntax_error_count >= 1
    assert all(symbol.name != "Broken.missing" for symbol in parsed.symbols)


def test_thrift_parser_spans_are_one_based_lines() -> None:
    parsed = parse()
    user = next(symbol for symbol in parsed.symbols if symbol.name == "User")
    assert user.span.start_line >= 1
    assert user.span.end_line >= user.span.start_line
    assert user.span.start_byte >= 0
