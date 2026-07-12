"""Framework-independent repository value objects and result models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class RepositoryId:
    """Stable opaque identifier for one canonical local repository."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"repo_[0-9a-f]{32}", self.value):
            raise ValueError("invalid repository ID")


@dataclass(frozen=True, slots=True)
class RepositoryLocation:
    """User-supplied or canonical filesystem location."""

    path: Path


@dataclass(frozen=True, slots=True)
class RepositoryPath:
    """Normalized POSIX path relative to a repository root."""

    value: PurePosixPath

    def __post_init__(self) -> None:
        text = self.value.as_posix()
        if "\x00" in text or "\\" in text:
            raise ValueError("invalid repository path")
        if self.value.is_absolute() or re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
            raise ValueError("repository path must be relative")
        if any(part == ".." or part.casefold() == ".git" for part in self.value.parts):
            raise ValueError("repository path is not permitted")

    @classmethod
    def root(cls) -> RepositoryPath:
        """Return the explicit repository root path."""
        return cls(PurePosixPath("."))

    @property
    def is_root(self) -> bool:
        """Return whether this path denotes the repository root."""
        return self.value == PurePosixPath(".")

    def as_posix(self) -> str:
        """Return an empty string for root or a normalized POSIX path."""
        return "" if self.is_root else self.value.as_posix()


@dataclass(frozen=True, slots=True)
class RepositoryDescriptor:
    """Registered repository metadata without exposing it as user content."""

    repository_id: RepositoryId
    canonical_location: RepositoryLocation
    name: str
    default_branch: str | None
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionRef:
    """Unresolved revision supplied at the application boundary."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or "\x00" in self.value or len(self.value) > 512:
            raise ValueError("invalid revision")
        if self.value.startswith("-"):
            raise ValueError("revision cannot be an option")


@dataclass(frozen=True, slots=True)
class ResolvedRevision:
    """Immutable commit identity resolved from a branch, tag, or SHA."""

    repository_id: RepositoryId
    commit_sha: str
    requested: RevisionRef

    def __post_init__(self) -> None:
        if _SHA_PATTERN.fullmatch(self.commit_sha) is None:
            raise ValueError("resolved revision must be a full commit SHA")


class RepositoryEntryType(StrEnum):
    """Git tree entry kinds exposed by M2."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SUBMODULE = "submodule"


@dataclass(frozen=True, slots=True)
class RepositoryEntry:
    """One entry in a commit tree."""

    path: RepositoryPath
    name: str
    entry_type: RepositoryEntryType
    object_id: str
    size: int | None = None
    unsupported_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FileContent:
    """Validated text read from a specific immutable commit."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    content: str
    encoding: str
    byte_size: int
    line_count: int
    content_hash: str
    truncated: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextSearchQuery:
    """Bounded text search request against one resolved commit."""

    pattern: str
    regex: bool = False
    case_sensitive: bool = True
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    path_prefix: RepositoryPath = field(default_factory=RepositoryPath.root)
    max_results: int = 100
    context_lines: int = 0

    def __post_init__(self) -> None:
        if not self.pattern or "\x00" in self.pattern:
            raise ValueError("search pattern is required")
        if self.max_results < 1 or self.context_lines < 0 or self.context_lines > 20:
            raise ValueError("invalid search limits")
        if any("\x00" in glob for glob in (*self.include_globs, *self.exclude_globs)):
            raise ValueError("invalid search glob")
        if self.regex:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError("invalid search regular expression") from error


@dataclass(frozen=True, slots=True)
class TextSearchHit:
    """One bounded match with immutable evidence coordinates."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    line: int
    excerpt: str
    match_start: int
    match_end: int
    truncated: bool = False
    context_before: tuple[str, ...] = ()
    context_after: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Commit metadata parsed from a fixed Git format."""

    repository_id: RepositoryId
    commit_sha: str
    parent_shas: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class FileHistoryEntry:
    """Commit metadata associated with one repository path."""

    commit: CommitInfo
    path: RepositoryPath


@dataclass(frozen=True, slots=True)
class BlameEntry:
    """A contiguous blame range tied to a commit SHA."""

    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    start_line: int
    line_count: int
    author_name: str
    authored_at: datetime
    content: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Bounded textual diff between immutable revisions."""

    repository_id: RepositoryId
    base_sha: str | None
    head_sha: str
    text: str
    byte_size: int
    truncated: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepositoryLimits:
    """Hard resource limits enforced by repository adapters."""

    max_repository_bytes: int = 2 * 1024**3
    max_text_file_bytes: int = 1024**2
    max_search_file_bytes: int = 1024**2
    max_directory_entries: int = 1_000
    max_search_results: int = 100
    max_diff_bytes: int = 2 * 1024**2
    max_command_output_bytes: int = 4 * 1024**2
    command_timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class RepositoryPolicy:
    """Read-only exclusion and Git history behavior policy."""

    limits: RepositoryLimits = field(default_factory=RepositoryLimits)
    detect_renames: bool = True
    deny_path_names: frozenset[str] = frozenset(
        {".git", ".env", "node_modules", "vendor", "build", "dist"}
    )
