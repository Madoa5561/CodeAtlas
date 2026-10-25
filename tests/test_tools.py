"""Behavior, authorization, and safety tests for the M4 retrieval tool executor."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import run_git

from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.adapters.repository.local_git import LocalGitRepositoryProvider
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.application.indexing import IndexingPolicy, TransactionalIndexingService
from codeatlas.application.tools import (
    ToolExecutionPolicy,
    ToolExecutor,
    ToolResult,
    build_tool_executor,
)
from codeatlas.application.tools.registry import RegisteredTool, ToolRegistry
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import ToolInput
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    ToolContext,
    ToolDefinition,
    ToolErrorCategory,
    ToolOutcome,
)
from codeatlas.domain.indexing_models import IndexingRequest
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
)
from codeatlas.domain.repository_ports import RepositoryRegistryPort
from codeatlas.domain.retrieval_models import RetrievalLimits
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
_BIG_TEXT = ("z" * 90 + "\n") * 1_000
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_TOOL_NAMES = {
    "compare_refs",
    "find_callees",
    "find_callers",
    "find_definition",
    "find_exports",
    "find_references",
    "find_symbol",
    "get_blame",
    "get_commit",
    "get_dependencies",
    "get_dependents",
    "get_diff",
    "get_file_history",
    "get_repository",
    "list_branches",
    "list_directory",
    "list_repositories",
    "list_tags",
    "read_file",
    "search_code",
    "search_commits",
    "search_text",
}


@dataclass(frozen=True, slots=True)
class ToolStack:
    """Indexed primary repository, registered secondary repository, and executor."""

    primary_root: Path
    primary: RepositoryDescriptor
    secondary: RepositoryDescriptor
    first_sha: str
    head_sha: str
    generation_id: str
    database_url: str
    executor: ToolExecutor
    query: SqliteIndexQuery
    store: SqliteIndexStore
    provider: LocalGitRepositoryProvider
    registry: RepositoryRegistryPort


def _make_repository(root: Path, *, files: tuple[str, ...]) -> tuple[str, str]:
    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "CodeAtlas Test")
    run_git(root, "config", "user.email", "tests@example.invalid")
    (root / "src").mkdir()
    for relative in files:
        (root / relative).write_text(_SERVICE_INITIAL, encoding="utf-8")
    (root / "notes.txt").write_text(_NOTES, encoding="utf-8")
    (root / "src" / "package.json").write_text(
        '{"name":"fixture","exports":{".":"./service.ts"}}',
        encoding="utf-8",
    )
    (root / "big.txt").write_text(_BIG_TEXT, encoding="utf-8")
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
    return first_sha, head_sha


@pytest.fixture
async def tool_stack(tmp_path: Path) -> AsyncIterator[ToolStack]:
    """Build a real indexed repository plus a second registered unindexed repository."""
    primary_root = tmp_path / "primary"
    first_sha, head_sha = _make_repository(primary_root, files=("src/service.ts",))
    secondary_root = tmp_path / "secondary"
    secondary_root.mkdir()
    run_git(secondary_root, "init", "-b", "main")
    run_git(secondary_root, "config", "user.name", "CodeAtlas Test")
    run_git(secondary_root, "config", "user.email", "tests@example.invalid")
    (secondary_root / "README.md").write_text("secondary\n", encoding="utf-8")
    run_git(secondary_root, "add", ".")
    run_git(secondary_root, "commit", "-m", "secondary commit")

    plugin = LocalGitRepositoryPlugin.from_settings(RepositorySettings())
    provider = plugin.provider
    registry = provider.registry
    database_url = f"sqlite:///{(tmp_path / 'index.db').as_posix()}"
    upgrade_database(database_url)
    store = SqliteIndexStore(database_url)
    service = TransactionalIndexingService(
        provider, store, (TreeSitterTypeScriptParser(),), IndexingPolicy()
    )
    primary = await provider.register_local_repository(RepositoryLocation(primary_root))
    secondary = await provider.register_local_repository(RepositoryLocation(secondary_root))
    result = await service.create_index(IndexingRequest(primary.repository_id, "main"))
    assert result.snapshot.commit_sha == head_sha
    query = SqliteIndexQuery(database_url)
    executor = build_tool_executor(repository=provider, registry=registry, index=query)
    try:
        yield ToolStack(
            primary_root=primary_root,
            primary=primary,
            secondary=secondary,
            first_sha=first_sha,
            head_sha=head_sha,
            generation_id=result.snapshot.generation_id.value,
            database_url=database_url,
            executor=executor,
            query=query,
            store=store,
            provider=provider,
            registry=registry,
        )
    finally:
        await query.aclose()
        store.engine.dispose()


def _call(name: str, arguments: dict[str, Any]) -> CanonicalToolCall:
    return CanonicalToolCall(call_id="call_1", tool_name=name, arguments_json=json.dumps(arguments))


async def _execute(
    stack: ToolStack,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    allowed: tuple[RepositoryId, ...] | None = None,
    repository_id: str | None = None,
) -> ToolOutcome:
    payload = dict(arguments or {})
    if repository_id is not None:
        payload["repository_id"] = repository_id
    context = ToolContext(
        request_id="req_1",
        allowed_repository_ids=((stack.primary.repository_id,) if allowed is None else allowed),
    )
    return await stack.executor.execute(_call(name, payload), context)


def _payload(outcome: ToolOutcome) -> dict[str, Any]:
    parsed = json.loads(outcome.payload_json)
    assert isinstance(parsed, dict)
    return parsed


def _assert_evidence(outcome: ToolOutcome, stack: ToolStack) -> None:
    assert outcome.ok, outcome.safe_message
    assert outcome.evidence
    for evidence in outcome.evidence:
        assert evidence.repository_id == stack.primary.repository_id
        assert _SHA_PATTERN.fullmatch(evidence.commit_sha) is not None
        assert evidence.path.is_root or evidence.path.as_posix()
        assert 1 <= evidence.start_line <= evidence.end_line
        assert evidence.excerpt.strip()
        assert evidence.retrieval_method
        assert len(evidence.retrieval_method) <= 64
        assert evidence.verified is True
        assert 0.0 <= evidence.confidence <= 1.0


async def test_catalog_definitions_match_strict_schemas(tool_stack: ToolStack) -> None:
    """Every tool has a stable name, description, and strict JSON input schema."""
    definitions = tool_stack.executor.catalog()
    assert {definition.name for definition in definitions} == EXPECTED_TOOL_NAMES
    assert len(definitions) == len(EXPECTED_TOOL_NAMES)
    schemas: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        assert definition.version == "1.0.0"
        assert definition.description.strip()
        assert len(definition.description) <= 1_000
        assert re.fullmatch(r"[a-z][a-z0-9_]{1,63}", definition.name)
        schema = json.loads(definition.input_schema_json)
        assert isinstance(schema, dict)
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        schemas[definition.name] = schema
    assert schemas["read_file"]["required"] == ["path"]
    assert set(schemas["compare_refs"]["required"]) == {"base", "head"}
    assert schemas["search_text"]["required"] == ["query"]
    assert schemas["search_code"]["required"] == ["query"]
    assert schemas["find_definition"]["required"] == ["name"]
    assert schemas["find_exports"].get("required", []) == []
    assert schemas["get_dependents"]["required"] == ["target"]
    assert schemas["get_file_history"]["required"] == ["path"]
    assert schemas["get_blame"]["required"] == ["path"]
    assert schemas["list_repositories"].get("required", []) == []
    assert schemas["find_symbol"].get("required", []) == []
    assert schemas["get_dependencies"].get("required", []) == []
    assert "revision" in schemas["read_file"]["properties"]
    assert "repository_id" in schemas["read_file"]["properties"]


async def test_evidence_bearing_tools_return_verified_provenance(tool_stack: ToolStack) -> None:
    """Content and graph tools return evidence with full SHA, path, and 1-based lines."""
    cases: tuple[tuple[str, dict[str, Any], str], ...] = (
        ("read_file", {"path": "src/service.ts"}, "read_file"),
        ("list_directory", {"path": "."}, "git_tree"),
        ("search_text", {"query": "helper"}, "git_text_search"),
        ("search_code", {"query": "helper"}, "index_text_search"),
        ("find_symbol", {"name": "helper"}, "index_symbol_exact"),
        ("find_definition", {"name": "Service"}, "index_definition"),
        ("find_exports", {"name": "Service"}, "index_export_symbol"),
        ("find_references", {"name": "helper"}, "index_reference"),
        ("find_callers", {"name": "helper"}, "index_call"),
        ("find_callees", {"symbol": "Service.run"}, "index_call"),
        ("get_dependencies", {"path": "src/service.ts"}, "index_import"),
        ("get_dependencies", {"symbol": "Service.run"}, "index_dependency"),
        ("get_dependents", {"target": "./service"}, "index_dependent"),
        ("get_commit", {}, "git_commit"),
        ("get_file_history", {"path": "src/service.ts"}, "git_file_history"),
        ("get_blame", {"path": "src/service.ts"}, "git_blame"),
        ("get_diff", {}, "git_diff"),
        (
            "compare_refs",
            {"base": "v1", "head": "main"},
            "git_compare",
        ),
    )
    for name, arguments, method in cases:
        outcome = await _execute(tool_stack, name, arguments)
        _assert_evidence(outcome, tool_stack)
        if name == "find_exports":
            assert all(
                evidence.retrieval_method.startswith("index_export")
                for evidence in outcome.evidence
            ), (name, [item.retrieval_method for item in outcome.evidence])
        else:
            assert all(
                evidence.retrieval_method.startswith(method) for evidence in outcome.evidence
            ), (name, [item.retrieval_method for item in outcome.evidence])


async def test_find_exports_reports_public_symbols(tool_stack: ToolStack) -> None:
    """Exported declarations are returned with verified provenance."""
    outcome = await _execute(tool_stack, "find_exports", {"name": "Service"})
    assert outcome.ok
    payload = _payload(outcome)
    assert payload["is_exported"] is True
    assert payload["is_publicly_exported"] is True
    assert payload["public_exports"]
    assert any(
        item["path"] == "src/service.ts" and item["exported"] is True
        for item in payload["exported_symbols"]
    )
    assert any(
        evidence.retrieval_method.startswith("index_export") for evidence in outcome.evidence
    )
    _assert_evidence(outcome, tool_stack)

    by_path = await _execute(tool_stack, "find_exports", {"path": "src/service.ts"})
    assert _payload(by_path)["is_exported"] is True


async def test_payloads_carry_expected_structure(tool_stack: ToolStack) -> None:
    """Representative payloads expose the fields a planner or reasoner needs."""
    repositories = _payload(await _execute(tool_stack, "list_repositories"))
    assert repositories["count"] == 1
    assert repositories["repositories"][0]["repository_id"] == (
        tool_stack.primary.repository_id.value
    )
    assert "canonical_location" not in repositories["repositories"][0]

    descriptor = _payload(await _execute(tool_stack, "get_repository"))
    assert descriptor["repository"]["repository_id"] == tool_stack.primary.repository_id.value
    assert descriptor["repository"]["default_branch"] == "main"

    branches = _payload(await _execute(tool_stack, "list_branches"))
    branch_by_name = {item["name"]: item["commit_sha"] for item in branches["branches"]}
    assert branch_by_name["main"] == tool_stack.head_sha

    tags = _payload(await _execute(tool_stack, "list_tags"))
    assert tags["tags"] == [{"name": "v1", "commit_sha": tool_stack.first_sha}]

    directory = _payload(await _execute(tool_stack, "list_directory"))
    assert directory["commit_sha"] == tool_stack.head_sha
    assert {item["name"] for item in directory["entries"]} == {"src", "notes.txt", "big.txt"}
    source_directory = _payload(await _execute(tool_stack, "list_directory", {"path": "src"}))
    assert {item["path"] for item in source_directory["entries"]} == {
        "src/service.ts",
        "src/other.ts",
        "src/package.json",
    }

    commit = _payload(await _execute(tool_stack, "get_commit"))
    assert commit["commit"]["commit_sha"] == tool_stack.head_sha

    search = _payload(await _execute(tool_stack, "search_commits", {"query": "initial commit"}))
    assert search["count"] == 1
    assert search["commits"][0]["commit_sha"] == tool_stack.first_sha

    history = _payload(await _execute(tool_stack, "get_file_history", {"path": "src/service.ts"}))
    assert history["count"] == 2
    assert history["history"][0]["commit_sha"] == tool_stack.head_sha

    symbols = _payload(await _execute(tool_stack, "find_symbol", {"name": "helper"}))
    assert symbols["count"] == 1
    assert symbols["symbols"][0]["qualified_name"] == "helper"
    assert symbols["symbols"][0]["path"] == "src/service.ts"

    code_hits = _payload(await _execute(tool_stack, "search_code", {"query": "needle second"}))
    assert code_hits["hits"] == [
        {
            "path": "notes.txt",
            "line": 2,
            "excerpt": "needle second line",
            "start_column": 0,
            "end_column": 13,
        }
    ]


async def test_revision_defaults_active_index_and_never_working_tree(
    tool_stack: ToolStack,
) -> None:
    """Omitted revisions resolve to the active index SHA, not the working tree."""
    head_read = await _execute(tool_stack, "read_file", {"path": "src/service.ts"})
    assert head_read.ok
    head_payload = _payload(head_read)
    assert head_payload["commit_sha"] == tool_stack.head_sha
    assert "class Service" in head_payload["content"]
    assert "working tree only" not in head_payload["content"]

    first_read = await _execute(
        tool_stack, "read_file", {"path": "src/service.ts", "revision": tool_stack.first_sha}
    )
    assert first_read.ok
    assert _payload(first_read)["commit_sha"] == tool_stack.first_sha
    assert _payload(first_read)["content"] == _SERVICE_INITIAL.rstrip("\n")

    head_commit = _payload(await _execute(tool_stack, "get_commit"))
    assert head_commit["commit"]["commit_sha"] == tool_stack.head_sha
    first_commit = _payload(
        await _execute(tool_stack, "get_commit", {"revision": tool_stack.first_sha})
    )
    assert first_commit["commit"]["commit_sha"] == tool_stack.first_sha
    tag_commit = _payload(await _execute(tool_stack, "get_commit", {"revision": "v1"}))
    assert tag_commit["commit"]["commit_sha"] == tool_stack.first_sha

    for bad_revision in ("HEAD~1", "does-not-exist", "@{0}"):
        outcome = await _execute(
            tool_stack, "read_file", {"path": "src/service.ts", "revision": bad_revision}
        )
        assert not outcome.ok
        assert outcome.error_category is ToolErrorCategory.INVALID_INPUT


async def test_path_containment_rejects_traversal(tool_stack: ToolStack) -> None:
    """Absolute paths, dot segments, backslashes, and dot-Git paths are rejected."""
    for bad_path in (
        "../outside.ts",
        "/etc/passwd",
        ".git/config",
        "src\\service.ts",
        "src/../secret.ts",
        ".",
    ):
        outcome = await _execute(tool_stack, "read_file", {"path": bad_path})
        assert not outcome.ok, bad_path
        assert outcome.error_category is ToolErrorCategory.INVALID_INPUT, bad_path
        assert outcome.payload_json == "{}"
        assert outcome.evidence == ()
        if len(bad_path) > 1:
            assert bad_path not in (outcome.safe_message or "")
    directory = await _execute(tool_stack, "list_directory", {"path": "../"})
    assert directory.error_category is ToolErrorCategory.INVALID_INPUT


async def test_repository_scope_and_default_resolution(tool_stack: ToolStack) -> None:
    """Repository selection honors authorization scope and safe ambiguity errors."""
    only_primary = (tool_stack.primary.repository_id,)
    both = (tool_stack.primary.repository_id, tool_stack.secondary.repository_id)

    default_read = await _execute(tool_stack, "read_file", {"path": "src/service.ts"})
    assert default_read.ok

    forbidden = await _execute(
        tool_stack,
        "list_branches",
        repository_id=tool_stack.secondary.repository_id.value,
    )
    assert forbidden.error_category is ToolErrorCategory.FORBIDDEN
    assert forbidden.evidence == ()

    unknown = await _execute(tool_stack, "list_branches", repository_id="repo_" + "0" * 32)
    assert unknown.error_category is ToolErrorCategory.NOT_FOUND

    malformed = await _execute(tool_stack, "list_branches", repository_id="not-a-repo")
    assert malformed.error_category is ToolErrorCategory.INVALID_INPUT

    ambiguous = await _execute(tool_stack, "list_branches", allowed=both)
    assert ambiguous.error_category is ToolErrorCategory.INVALID_INPUT
    assert "repository_id" in (ambiguous.safe_message or "")

    secondary_descriptor = await _execute(
        tool_stack,
        "get_repository",
        allowed=both,
        repository_id=tool_stack.secondary.repository_id.value,
    )
    assert secondary_descriptor.ok

    scoped_catalog = _payload(await _execute(tool_stack, "list_repositories", allowed=only_primary))
    assert scoped_catalog["count"] == 1
    open_catalog = _payload(await _execute(tool_stack, "list_repositories", allowed=()))
    assert open_catalog["count"] == 2
    unscoped_request = await _execute(
        tool_stack,
        "list_branches",
        allowed=(),
        repository_id=tool_stack.primary.repository_id.value,
    )
    assert unscoped_request.ok
    unauthorized_scope = await _execute(
        tool_stack,
        "list_repositories",
        allowed=(RepositoryId("repo_" + "2" * 32),),
    )
    assert _payload(unauthorized_scope)["count"] == 0


async def test_invalid_arguments_and_unknown_tools_fail_safely(tool_stack: ToolStack) -> None:
    """Unknown tools, strict type violations, and bad JSON never reach a handler."""
    unknown = await _execute(tool_stack, "delete_repository", {})
    assert unknown.error_category is ToolErrorCategory.NOT_FOUND
    assert unknown.payload_json == "{}"
    assert unknown.evidence == ()

    invalid_arguments: tuple[tuple[str, dict[str, Any]], ...] = (
        ("read_file", {}),
        ("read_file", {"path": 5}),
        ("read_file", {"path": "src/service.ts", "extra": True}),
        ("read_file", {"path": "src/service.ts", "start_line": "2"}),
        (
            "get_dependencies",
            {"symbol": "helper", "path": "src/service.ts"},
        ),
        ("get_dependencies", {}),
        ("compare_refs", {"base": "HEAD"}),
        ("find_symbol", {}),
        ("search_text", {"query": ""}),
        ("get_blame", {"path": "src/service.ts", "start_line": 5, "end_line": 2}),
    )
    for name, arguments in invalid_arguments:
        outcome = await _execute(tool_stack, name, arguments)
        assert not outcome.ok, (name, arguments)
        assert outcome.error_category is ToolErrorCategory.INVALID_INPUT, (name, arguments)
        assert outcome.payload_json == "{}"
        assert outcome.evidence == ()
        message = outcome.safe_message or ""
        assert "pydantic" not in message.lower()
        assert "Traceback" not in message
        assert "ValidationError" not in message

    with pytest.raises(ValueError):
        CanonicalToolCall(call_id="call_1", tool_name="read_file", arguments_json="{bad json")
    with pytest.raises(ValueError):
        CanonicalToolCall(
            call_id="call_1",
            tool_name="read_file",
            arguments_json=json.dumps(["not", "an", "object"]),
        )


async def test_line_ranges_are_respected_and_bounded(tool_stack: ToolStack) -> None:
    """Explicit line ranges narrow content and evidence spans."""
    outcome = await _execute(
        tool_stack,
        "read_file",
        {"path": "src/service.ts", "start_line": 3, "end_line": 3},
    )
    assert outcome.ok
    payload = _payload(outcome)
    assert payload["start_line"] == 3
    assert payload["end_line"] == 3
    assert payload["content"] == "  run(): number { return helper(); }"
    assert payload["truncated"] is True
    assert len(outcome.evidence) == 1
    assert outcome.evidence[0].start_line == 3
    assert outcome.evidence[0].end_line == 3

    out_of_range = await _execute(
        tool_stack, "read_file", {"path": "src/service.ts", "start_line": 999}
    )
    assert out_of_range.error_category is ToolErrorCategory.INVALID_INPUT


async def test_search_text_evidence_lines_match_hits(tool_stack: ToolStack) -> None:
    """Git text search evidence line spans match the reported hit lines."""
    outcome = await _execute(tool_stack, "search_text", {"query": "needle second"})
    assert outcome.ok
    payload = _payload(outcome)
    assert payload["hits"] == [
        {
            "path": "notes.txt",
            "line": 2,
            "excerpt": "needle second line",
            "start_column": 0,
            "end_column": 13,
        }
    ]
    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.path.as_posix() == "notes.txt"
    assert evidence.start_line == 2
    assert evidence.end_line == 2
    assert evidence.commit_sha == tool_stack.head_sha
    assert evidence.retrieval_method == "git_text_search"

    case_insensitive = await _execute(
        tool_stack, "search_text", {"query": "NEEDLE SECOND", "case_sensitive": False}
    )
    assert _payload(case_insensitive)["count"] == 1
    case_sensitive = await _execute(
        tool_stack, "search_text", {"query": "NEEDLE SECOND", "case_sensitive": True}
    )
    assert _payload(case_sensitive)["count"] == 0


async def test_find_symbol_kinds_and_limits(tool_stack: ToolStack) -> None:
    """Kind filters and result limits bound symbol lookups."""
    function_symbols = await _execute(
        tool_stack, "find_symbol", {"name": "helper", "kinds": ["function"]}
    )
    assert function_symbols.ok
    assert _payload(function_symbols)["count"] == 1
    assert function_symbols.evidence[0].symbol_name == "helper"
    assert function_symbols.evidence[0].symbol_id is not None

    wrong_kind = await _execute(tool_stack, "find_symbol", {"name": "helper", "kinds": ["class"]})
    assert _payload(wrong_kind)["count"] == 0

    qualified = await _execute(tool_stack, "find_symbol", {"qualified_name": "run"})
    assert _payload(qualified)["symbols"][0]["qualified_name"] == "Service.run"

    limited = await _execute(
        tool_stack,
        "find_symbol",
        {"kinds": ["class", "function", "method", "variable"], "limit": 2},
    )
    assert limited.ok
    assert _payload(limited)["count"] == 2
    assert limited.truncated is True

    missing_definition = await _execute(
        tool_stack, "find_definition", {"name": "definitely_absent"}
    )
    assert missing_definition.ok
    assert _payload(missing_definition)["count"] == 0
    assert missing_definition.evidence == ()


async def test_payload_cap_and_default_content_truncation(tool_stack: ToolStack) -> None:
    """Bounded content and the payload cap both set the truncation flag."""
    default_read = await _execute(tool_stack, "read_file", {"path": "big.txt"})
    assert default_read.ok
    default_payload = _payload(default_read)
    assert default_payload["truncated"] is True
    assert default_read.truncated is True
    assert len(default_payload["content"]) <= 40_000
    assert len(default_read.payload_json) <= 60_000

    capped_executor = build_tool_executor(
        repository=tool_stack.provider,
        registry=tool_stack.registry,
        index=tool_stack.query,
        policy=ToolExecutionPolicy(max_payload_chars=1_000, timeout_seconds=10.0),
    )
    capped = await capped_executor.execute(
        _call("read_file", {"path": "big.txt"}),
        ToolContext(
            request_id="req_1",
            allowed_repository_ids=(tool_stack.primary.repository_id,),
        ),
    )
    assert capped.ok
    assert capped.truncated is True
    assert len(capped.payload_json) <= 1_000
    capped_payload = json.loads(capped.payload_json)
    assert capped_payload["truncated"] is True
    assert capped_payload["path"] == "big.txt"


async def test_timeout_is_bounded_and_retried(tool_stack: ToolStack) -> None:
    """A slow tool yields a bounded timeout outcome with at most one retry."""
    attempts: list[int] = []

    async def slow_handler(
        services: ToolServices, context: ToolContext, arguments: ToolInput
    ) -> ToolResult:
        attempts.append(1)
        await asyncio.sleep(0.5)
        return ToolResult({"unreachable": True})

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            definition=ToolDefinition(
                name="slow_tool",
                description="Slow fixture tool used to verify the execution timeout.",
                input_schema_json='{"type":"object","additionalProperties":false}',
            ),
            input_model=ToolInput,
            handler=slow_handler,
        )
    )
    services = ToolServices(
        repository=tool_stack.provider,
        registry=tool_stack.registry,
        index=tool_stack.query,
        limits=RetrievalLimits(),
    )
    executor = ToolExecutor(
        registry,
        services,
        ToolExecutionPolicy(timeout_seconds=0.05, max_attempts=2),
    )
    started = time.monotonic()
    outcome = await executor.execute(
        CanonicalToolCall(call_id="call_1", tool_name="slow_tool", arguments_json="{}"),
        ToolContext(request_id="req_1"),
    )
    elapsed = time.monotonic() - started
    assert not outcome.ok
    assert outcome.error_category is ToolErrorCategory.TIMEOUT
    assert outcome.retryable is True
    assert len(attempts) == 2
    assert elapsed < 2.0

    attempts.clear()
    single = ToolExecutor(
        registry,
        services,
        ToolExecutionPolicy(timeout_seconds=0.05, max_attempts=1),
    )
    single_outcome = await single.execute(
        CanonicalToolCall(call_id="call_1", tool_name="slow_tool", arguments_json="{}"),
        ToolContext(request_id="req_1"),
    )
    assert single_outcome.error_category is ToolErrorCategory.TIMEOUT
    assert len(attempts) == 1


async def test_execution_is_deterministic_and_stateless(tool_stack: ToolStack) -> None:
    """Repeated read-only calls produce identical payloads and evidence IDs."""
    first = await _execute(tool_stack, "search_code", {"query": "helper"})
    second = await _execute(tool_stack, "search_code", {"query": "helper"})
    assert first.payload_json == second.payload_json
    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]
    assert first.truncated is False

    for _ in range(3):
        outcome = await _execute(tool_stack, "list_repositories")
        assert outcome.ok
        assert _payload(outcome)["count"] == 1


async def test_graph_tools_payloads_and_evidence(tool_stack: ToolStack) -> None:
    """Dependency, caller, and callee tools expose relation provenance."""
    dependencies = await _execute(tool_stack, "get_dependencies", {"path": "src/service.ts"})
    assert dependencies.ok
    dependency_payload = _payload(dependencies)
    assert [item["raw_target"] for item in dependency_payload["dependencies"]] == ['"./helper"']
    assert dependency_payload["dependencies"][0]["path"] == "src/service.ts"
    assert dependencies.evidence[0].retrieval_method == "index_import"

    dependents = await _execute(tool_stack, "get_dependents", {"target": "./service"})
    assert dependents.ok
    dependents_payload = _payload(dependents)
    assert dependents_payload["dependents"][0]["path"] == "src/other.ts"

    heritage = await _execute(tool_stack, "get_dependents", {"target": "Base"})
    assert heritage.ok
    heritage_targets = [item["raw_target"] for item in _payload(heritage)["dependents"]]
    assert any("Base" in target for target in heritage_targets)

    callers = await _execute(tool_stack, "find_callers", {"name": "helper"})
    assert callers.ok
    caller_payload = _payload(callers)
    assert caller_payload["callers"][0]["caller_symbol_name"] == "Service.run"
    assert caller_payload["callers"][0]["raw_target"] == "helper"

    callees = await _execute(tool_stack, "find_callees", {"symbol": "Service.run"})
    assert callees.ok
    callee_payload = _payload(callees)
    assert [item["raw_target"] for item in callee_payload["callees"]] == ["helper"]
    assert callee_payload["symbols"][0]["qualified_name"] == "Service.run"


async def test_history_and_diff_tools(tool_stack: ToolStack) -> None:
    """History, blame, commit search, and diffs are commit-pinned and bounded."""
    blame = await _execute(tool_stack, "get_blame", {"path": "src/service.ts"})
    assert blame.ok
    blame_payload = _payload(blame)
    assert blame_payload["ranges"]
    assert blame_payload["ranges"][0]["commit_sha"] == tool_stack.head_sha
    assert blame_payload["ranges"][0]["start_line"] == 1
    assert all(_SHA_PATTERN.fullmatch(item.commit_sha) for item in blame.evidence)

    diff = await _execute(tool_stack, "get_diff")
    assert diff.ok
    diff_payload = _payload(diff)
    assert diff_payload["commit_sha"] == tool_stack.head_sha
    assert diff_payload["diff"]["head_sha"] == tool_stack.head_sha
    assert diff_payload["diff"]["text"]
    assert len(diff_payload["diff"]["text"]) <= 40_000

    comparison = await _execute(tool_stack, "compare_refs", {"base": "v1", "head": "main"})
    assert comparison.ok
    comparison_payload = _payload(comparison)
    assert comparison_payload["base_sha"] == tool_stack.first_sha
    assert comparison_payload["head_sha"] == tool_stack.head_sha
    assert comparison_payload["diff"]["text"]
    assert comparison.evidence[0].commit_sha == tool_stack.head_sha

    same_ref = await _execute(tool_stack, "compare_refs", {"base": "main", "head": "main"})
    assert same_ref.ok
    assert _payload(same_ref)["diff"]["text"] == ""
    assert same_ref.evidence == ()

    history = await _execute(tool_stack, "get_file_history", {"path": "src/service.ts"})
    assert history.ok
    assert [item.commit_sha for item in history.evidence] == [
        tool_stack.head_sha,
        tool_stack.first_sha,
    ]
    assert all(item.path.as_posix() == "src/service.ts" for item in history.evidence)
