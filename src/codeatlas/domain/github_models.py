"""Framework-independent read-only GitHub models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True, slots=True)
class GitHubRepositoryRef:
    """GitHub owner/name pair used by read-only tools."""

    owner: str
    name: str

    def __post_init__(self) -> None:
        if _SLUG.fullmatch(self.owner) is None or _SLUG.fullmatch(self.name) is None:
            raise ValueError("invalid GitHub repository reference")

    @classmethod
    def parse(cls, value: str) -> GitHubRepositoryRef:
        """Parse an ``owner/name`` string."""
        parts = value.strip().split("/")
        if len(parts) != 2:
            raise ValueError("GitHub repository must use owner/name")
        return cls(owner=parts[0], name=parts[1])

    @property
    def full_name(self) -> str:
        """Return the canonical owner/name display value."""
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class GitHubRepositoryInfo:
    """Read-only metadata for one GitHub repository."""

    full_name: str
    description: str | None
    default_branch: str | None
    url: str
    private: bool


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """Bounded pull request metadata returned by the read-only adapter."""

    number: int
    title: str
    state: str
    url: str
    head_sha: str
    base_sha: str
    author: str | None
    body: str
    created_at: datetime | None
    updated_at: datetime | None
    merged: bool
    additions: int
    deletions: int
    changed_files: int

    def __post_init__(self) -> None:
        if (
            self.number < 1
            or not self.title.strip()
            or not self.url.startswith(("https://", "http://"))
        ):
            raise ValueError("invalid pull request")
        if len(self.head_sha) != 40 or len(self.base_sha) != 40:
            raise ValueError("pull request revisions must be full SHAs")


@dataclass(frozen=True, slots=True)
class IssueInfo:
    """Bounded issue metadata returned by the read-only adapter."""

    number: int
    title: str
    state: str
    url: str
    author: str | None
    body: str
    labels: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.number < 1
            or not self.title.strip()
            or not self.url.startswith(("https://", "http://"))
        ):
            raise ValueError("invalid issue")


@dataclass(frozen=True, slots=True)
class PullRequestDiff:
    """Bounded unified diff for one pull request."""

    number: int
    head_sha: str
    url: str
    diff: str

    def __post_init__(self) -> None:
        if self.number < 1 or len(self.head_sha) != 40:
            raise ValueError("invalid pull request diff")
        if not self.diff.strip():
            raise ValueError("empty pull request diff")
