"""Tool catalog: stable names, model-facing JSON schemas, and handler bindings."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from codeatlas.application.tools import (
    git_tools,
    github_tools,
    index_tools,
    repository_tools,
)
from codeatlas.application.tools.registry import RegisteredTool, ToolRegistry
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import (
    CompareRefsInput,
    FindCalleesInput,
    FindCallersInput,
    FindDefinitionInput,
    FindExportsInput,
    FindReferencesInput,
    FindSymbolInput,
    GetBlameInput,
    GetCommitInput,
    GetDependenciesInput,
    GetDependentsInput,
    GetDiffInput,
    GetFileHistoryInput,
    GetPullRequestDiffInput,
    GetPullRequestInput,
    GetRepositoryInput,
    ListBranchesInput,
    ListDirectoryInput,
    ListIssuesInput,
    ListRepositoriesInput,
    ListTagsInput,
    ReadFileInput,
    SearchCodeInput,
    SearchCommitsInput,
    SearchLexicalInput,
    SearchTextInput,
    ToolResult,
    TraverseGraphInput,
)
from codeatlas.domain.agent_models import ToolContext, ToolDefinition

InputT = TypeVar("InputT", bound=BaseModel)

TOOL_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ToolSpec(Generic[InputT]):
    """One catalog entry before it is bound to the executor registry."""

    name: str
    description: str
    input_model: type[InputT]
    handler: Callable[[ToolServices, ToolContext, InputT], Awaitable[ToolResult]]


def _input_schema(model: type[BaseModel]) -> str:
    return json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bind(spec: ToolSpec[InputT]) -> RegisteredTool:
    definition = ToolDefinition(
        name=spec.name,
        description=spec.description,
        input_schema_json=_input_schema(spec.input_model),
        version=TOOL_VERSION,
    )

    async def invoke(
        services: ToolServices, context: ToolContext, arguments: BaseModel
    ) -> ToolResult:
        return await spec.handler(services, context, cast(InputT, arguments))

    return RegisteredTool(definition=definition, input_model=spec.input_model, handler=invoke)


_REPOSITORY_ARG = "repository_id is optional and defaults to the only authorized repository."

TOOL_SPECS: tuple[ToolSpec[Any], ...] = (
    ToolSpec(
        "list_repositories",
        "List registered repositories authorized for this request with safe metadata. "
        "Use this first when the repository is unknown.",
        ListRepositoriesInput,
        repository_tools.list_repositories,
    ),
    ToolSpec(
        "get_repository",
        "Return one registered repository descriptor (ID, name, default branch, "
        "registration time). " + _REPOSITORY_ARG,
        GetRepositoryInput,
        repository_tools.get_repository,
    ),
    ToolSpec(
        "list_branches",
        "List branches resolved to full immutable commit SHAs. " + _REPOSITORY_ARG,
        ListBranchesInput,
        repository_tools.list_branches,
    ),
    ToolSpec(
        "list_tags",
        "List tags peeled and resolved to full immutable commit SHAs. " + _REPOSITORY_ARG,
        ListTagsInput,
        repository_tools.list_tags,
    ),
    ToolSpec(
        "list_directory",
        "List one commit tree directory without reading the working tree. Path '.' is the "
        "repository root. " + _REPOSITORY_ARG,
        ListDirectoryInput,
        repository_tools.list_directory,
    ),
    ToolSpec(
        "read_file",
        "Read a bounded line range of one text file at the resolved commit SHA. "
        "Paths are POSIX paths relative to the repository root; absolute paths, '..' and "
        "'\\' are rejected. " + _REPOSITORY_ARG,
        ReadFileInput,
        repository_tools.read_file,
    ),
    ToolSpec(
        "search_text",
        "Case-sensitive or insensitive literal substring search over a commit-pinned "
        "repository snapshot with bounded excerpts. " + _REPOSITORY_ARG,
        SearchTextInput,
        repository_tools.search_text,
    ),
    ToolSpec(
        "search_code",
        "Literal substring search over safe file content stored in the active code index. "
        "Faster than search_text and returns index line/column excerpts. " + _REPOSITORY_ARG,
        SearchCodeInput,
        repository_tools.search_code,
    ),
    ToolSpec(
        "search_lexical",
        "BM25-ranked lexical search over safe file content stored in the active code index. "
        "Use alongside literal search for broader identifier/documentation recall. "
        + _REPOSITORY_ARG,
        SearchLexicalInput,
        index_tools.search_lexical,
    ),
    ToolSpec(
        "find_symbol",
        "Find declarations by exact name and/or qualified-name suffix with optional kind "
        "and path-prefix filters. " + _REPOSITORY_ARG,
        FindSymbolInput,
        index_tools.find_symbol,
    ),
    ToolSpec(
        "find_definition",
        "Find the declaration of one exact symbol name, falling back to a qualified-name "
        "suffix match. " + _REPOSITORY_ARG,
        FindDefinitionInput,
        index_tools.find_definition,
    ),
    ToolSpec(
        "find_exports",
        "Check whether a symbol is exported and list export relations for a symbol or file. "
        "Use this for questions about public/re-exported APIs. " + _REPOSITORY_ARG,
        FindExportsInput,
        index_tools.find_exports,
    ),
    ToolSpec(
        "find_references",
        "Find incoming references (call, extends, implements, reference relations) whose "
        "raw target matches a symbol name. " + _REPOSITORY_ARG,
        FindReferencesInput,
        index_tools.find_references,
    ),
    ToolSpec(
        "traverse_graph",
        "Traverse call/dependency/reference relations from a symbol or file with bounded "
        "depth, edge kinds, and result count. " + _REPOSITORY_ARG,
        TraverseGraphInput,
        index_tools.traverse_graph,
    ),
    ToolSpec(
        "get_pull_request",
        "Read one GitHub pull request by number (title, state, revisions, body) as "
        "external evidence. " + _REPOSITORY_ARG,
        GetPullRequestInput,
        github_tools.get_pull_request,
    ),
    ToolSpec(
        "get_pull_request_diff",
        "Read the bounded unified diff of one GitHub pull request. " + _REPOSITORY_ARG,
        GetPullRequestDiffInput,
        github_tools.get_pull_request_diff,
    ),
    ToolSpec(
        "list_issues",
        "List bounded GitHub issues (excluding pull requests) for review context. "
        + _REPOSITORY_ARG,
        ListIssuesInput,
        github_tools.list_issues,
    ),
    ToolSpec(
        "find_callers",
        "Find call sites whose callee target matches a name or dotted suffix. " + _REPOSITORY_ARG,
        FindCallersInput,
        index_tools.find_callers,
    ),
    ToolSpec(
        "find_callees",
        "Find outgoing call relations of one declaration. " + _REPOSITORY_ARG,
        FindCalleesInput,
        index_tools.find_callees,
    ),
    ToolSpec(
        "get_dependencies",
        "Return import dependencies of a file or directory path, or outgoing relations of a "
        "symbol. Exactly one of symbol or path is required. " + _REPOSITORY_ARG,
        GetDependenciesInput,
        index_tools.get_dependencies,
    ),
    ToolSpec(
        "get_dependents",
        "Find relations that target a symbol name or import specifier (who depends on it). "
        + _REPOSITORY_ARG,
        GetDependentsInput,
        index_tools.get_dependents,
    ),
    ToolSpec(
        "get_commit",
        "Return metadata for one immutable commit resolved from branch, tag, or SHA. "
        + _REPOSITORY_ARG,
        GetCommitInput,
        git_tools.get_commit,
    ),
    ToolSpec(
        "search_commits",
        "Search bounded commit metadata by message text, author, date range, and path. "
        + _REPOSITORY_ARG,
        SearchCommitsInput,
        git_tools.search_commits,
    ),
    ToolSpec(
        "get_file_history",
        "Return bounded commit history for one path with optional rename following. "
        + _REPOSITORY_ARG,
        GetFileHistoryInput,
        git_tools.get_file_history,
    ),
    ToolSpec(
        "get_blame",
        "Return commit-pinned line attribution ranges for one path. " + _REPOSITORY_ARG,
        GetBlameInput,
        git_tools.get_blame,
    ),
    ToolSpec(
        "compare_refs",
        "Compare two revisions of the same repository with safe, bounded diffs. " + _REPOSITORY_ARG,
        CompareRefsInput,
        git_tools.compare_refs,
    ),
    ToolSpec(
        "get_diff",
        "Return the bounded diff of one commit against its first parent. " + _REPOSITORY_ARG,
        GetDiffInput,
        git_tools.get_diff,
    ),
)


def build_tool_registry() -> ToolRegistry:
    """Build the immutable default registry with every M4 retrieval tool."""
    registry = ToolRegistry()
    for spec in TOOL_SPECS:
        registry.register(_bind(spec))
    return registry
