"""Ports for the read-only GitHub integration."""

from __future__ import annotations

from typing import Protocol

from codeatlas.domain.github_models import (
    GitHubRepositoryInfo,
    GitHubRepositoryRef,
    IssueInfo,
    PullRequestDiff,
    PullRequestInfo,
)


class GitHubReadOnlyPort(Protocol):
    """Bounded read-only GitHub API operations."""

    async def get_repository(self, repository: GitHubRepositoryRef) -> GitHubRepositoryInfo: ...

    async def get_pull_request(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestInfo: ...

    async def get_pull_request_diff(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestDiff: ...

    async def list_issues(
        self,
        repository: GitHubRepositoryRef,
        *,
        state: str,
        limit: int,
    ) -> tuple[IssueInfo, ...]: ...

    async def aclose(self) -> None: ...
