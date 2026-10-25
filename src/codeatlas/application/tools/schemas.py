"""Strict Pydantic input schemas and result envelope for retrieval tools.

These models are the adapter boundary for model-provided tool arguments. They
use Pydantic v2 strict mode and ``extra="forbid"`` so a provider cannot smuggle
unexpected keys or rely on loose string-to-number coercion. JSON Schema for model
prompting is generated from these same models in ``catalog.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codeatlas.domain.agent_models import EvidenceCandidate

SymbolKindName = Literal[
    "class",
    "interface",
    "function",
    "method",
    "constructor",
    "type_alias",
    "enum",
    "variable",
    "namespace",
    "anonymous_function",
    "service",
    "struct",
    "union",
    "exception",
    "field",
    "typedef",
    "const",
]

RelationKindName = Literal["import", "export", "call", "extends", "implements", "reference"]

_DEFAULT_EDGE_KINDS: tuple[RelationKindName, ...] = (
    "import",
    "export",
    "call",
    "extends",
    "implements",
    "reference",
)

_REPOSITORY_ID_PATTERN = r"^repo_[0-9a-f]{32}$"


class ToolInput(BaseModel):
    """Base for every tool argument model; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RepositoryScopedInput(ToolInput):
    """Common optional repository selection for repository-bound tools."""

    repository_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=_REPOSITORY_ID_PATTERN,
        description="Registered repository ID; defaults to the only authorized repository.",
    )


class RevisionScopedInput(RepositoryScopedInput):
    """Optional immutable revision selection resolved by the tool."""

    revision: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Branch, tag, or commit SHA; defaults to the active index commit.",
    )


class ListRepositoriesInput(ToolInput):
    """No arguments; lists only repositories authorized for this request."""


class GetRepositoryInput(RepositoryScopedInput):
    """Select one repository descriptor."""


class ListBranchesInput(RepositoryScopedInput):
    """List branches resolved to commit SHAs."""

    limit: int = Field(default=50, ge=1, le=200)


class ListTagsInput(RepositoryScopedInput):
    """List tags peeled to commit SHAs."""

    limit: int = Field(default=50, ge=1, le=200)


class ListDirectoryInput(RevisionScopedInput):
    """List one commit tree directory."""

    path: str = Field(
        default=".",
        min_length=1,
        max_length=4096,
        description="POSIX repository-relative directory path; '.' is the root.",
    )
    limit: int = Field(default=50, ge=1, le=200)


class ReadFileInput(RevisionScopedInput):
    """Read a bounded line range from one commit-pinned text file."""

    path: str = Field(min_length=1, max_length=4096)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=200, le=200_000)

    @model_validator(mode="after")
    def require_ordered_range(self) -> Self:
        """Reject a reversed explicit line range before execution."""
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must not precede start_line")
        return self


class SearchTextInput(RevisionScopedInput):
    """Literal text search over commit-pinned repository content."""

    query: str = Field(min_length=1, max_length=1_024)
    case_sensitive: bool = False
    path_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class SearchCodeInput(RepositoryScopedInput):
    """Literal text search over safe content stored in the active index."""

    query: str = Field(min_length=1, max_length=1_024)
    case_sensitive: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class SearchLexicalInput(RepositoryScopedInput):
    """BM25 lexical search over safe content stored in the active index."""

    query: str = Field(min_length=1, max_length=1_024)
    path_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class SearchVectorInput(RepositoryScopedInput):
    """Vector similarity search over the active Qdrant projection."""

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=50)


class FetchHttpInput(RepositoryScopedInput):
    """Fetch one allowlisted public HTTP document."""

    url: str = Field(
        min_length=8,
        max_length=2_048,
        pattern=r"^https?://[^\s]+$",
    )
    method: Literal["GET", "HEAD"] = "GET"


class FindSymbolInput(RepositoryScopedInput):
    """Find declarations by exact name or qualified-name suffix."""

    name: str | None = Field(default=None, min_length=1, max_length=512)
    qualified_name: str | None = Field(default=None, min_length=1, max_length=1_024)
    kinds: list[SymbolKindName] = Field(default_factory=list, max_length=10)
    path_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_name_or_qualified(self) -> Self:
        """Require at least one name filter so the query stays bounded."""
        if self.name is None and self.qualified_name is None:
            if not self.kinds:
                raise ValueError("name or qualified_name is required")
        return self


class FindDefinitionInput(RepositoryScopedInput):
    """Find the declaration of one exact symbol name."""

    name: str = Field(min_length=1, max_length=512)
    kinds: list[SymbolKindName] = Field(default_factory=list, max_length=10)
    path_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class FindExportsInput(RepositoryScopedInput):
    """Check exported declarations and export relations for a symbol or file."""

    name: str | None = Field(default=None, min_length=1, max_length=512)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_name_or_path(self) -> Self:
        """Require a symbol name or file path so the query stays bounded."""
        if self.name is None and self.path is None:
            raise ValueError("name or path is required")
        return self


class FindReferencesInput(RepositoryScopedInput):
    """Find incoming parser-derived references to a target name."""

    name: str = Field(min_length=1, max_length=512)
    qualified_name: str | None = Field(default=None, min_length=1, max_length=1_024)
    limit: int = Field(default=20, ge=1, le=100)


class FindCallersInput(RepositoryScopedInput):
    """Find call sites whose raw target matches a callee name."""

    name: str = Field(min_length=1, max_length=1_024)
    limit: int = Field(default=20, ge=1, le=100)


class FindCalleesInput(RepositoryScopedInput):
    """Find outgoing call relations of one declaration."""

    symbol: str = Field(min_length=1, max_length=1_024)
    limit: int = Field(default=20, ge=1, le=100)


class GetDependenciesInput(RepositoryScopedInput):
    """Return import dependencies of a file/directory or relations of a symbol."""

    symbol: str | None = Field(default=None, min_length=1, max_length=1_024)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def require_exactly_one_target(self) -> Self:
        """Require exactly one of symbol or path."""
        if (self.symbol is None) == (self.path is None):
            raise ValueError("exactly one of symbol or path is required")
        return self


class GetDependentsInput(RepositoryScopedInput):
    """Find relations that target a symbol name or import specifier."""

    target: str = Field(min_length=1, max_length=1_024)
    limit: int = Field(default=20, ge=1, le=100)


class TraverseGraphInput(RepositoryScopedInput):
    """Bounded call/dependency graph traversal from a symbol or file."""

    start: str = Field(min_length=1, max_length=1_024)
    start_type: Literal["symbol", "file"] = "symbol"
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    edge_kinds: list[RelationKindName] = Field(
        default_factory=lambda: list(_DEFAULT_EDGE_KINDS),
        max_length=6,
    )
    max_depth: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=50, ge=1, le=100)


_GITHUB_SLUG_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"


class GitHubScopedInput(RepositoryScopedInput):
    """Optional GitHub owner/name selection with the configured default."""

    repository: str | None = Field(
        default=None,
        pattern=_GITHUB_SLUG_PATTERN,
        description="GitHub owner/name; defaults to the configured repository.",
    )


class GetPullRequestInput(GitHubScopedInput):
    """Read one pull request by number."""

    number: str = Field(min_length=1, max_length=7, pattern=r"^[0-9]+$")


class GetPullRequestDiffInput(GitHubScopedInput):
    """Read the bounded unified diff of one pull request."""

    number: str = Field(min_length=1, max_length=7, pattern=r"^[0-9]+$")


class ListIssuesInput(GitHubScopedInput):
    """List bounded issues for one GitHub repository."""

    state: Literal["open", "closed", "all"] = "open"
    limit: int = Field(default=10, ge=1, le=30)


class GetCommitInput(RevisionScopedInput):
    """Read metadata for one commit."""


class SearchCommitsInput(RevisionScopedInput):
    """Search bounded commit metadata."""

    query: str = Field(default="", max_length=1_024)
    author: str | None = Field(default=None, min_length=1, max_length=200)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    since: str | None = Field(default=None, min_length=1, max_length=40)
    until: str | None = Field(default=None, min_length=1, max_length=40)
    limit: int = Field(default=20, ge=1, le=100)


class GetFileHistoryInput(RevisionScopedInput):
    """Return bounded commit history for one path."""

    path: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class GetBlameInput(RevisionScopedInput):
    """Return bounded line attribution ranges for one path."""

    path: str = Field(min_length=1, max_length=4096)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def require_ordered_range(self) -> Self:
        """Reject a reversed explicit line range before execution."""
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must not precede start_line")
        return self


class CompareRefsInput(RepositoryScopedInput):
    """Compare two revisions of the same repository."""

    base: str = Field(min_length=1, max_length=512)
    head: str = Field(min_length=1, max_length=512)


class GetDiffInput(RevisionScopedInput):
    """Return the bounded diff of one commit against its first parent."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Internal successful tool output before the executor bounds its payload."""

    payload: dict[str, Any]
    evidence: tuple[EvidenceCandidate, ...] = ()
    truncated: bool = False
