"""GitHub read-only adapter and external evidence tool tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from codeatlas.adapters.github.client import GitHubRestClient
from codeatlas.application.tools.github_tools import (
    get_pull_request,
    get_pull_request_diff,
    list_issues,
)
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import (
    GetPullRequestDiffInput,
    GetPullRequestInput,
    ListIssuesInput,
)
from codeatlas.config.settings import GitHubSettings
from codeatlas.domain.agent_models import EvidenceKind, ToolContext
from codeatlas.domain.errors import GitHubAuthenticationError, GitHubNotFoundError
from codeatlas.domain.github_models import (
    GitHubRepositoryInfo,
    GitHubRepositoryRef,
    IssueInfo,
    PullRequestDiff,
    PullRequestInfo,
)
from codeatlas.domain.indexing_models import IndexGenerationStatus
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
)
from codeatlas.domain.retrieval_models import IndexSnapshotSummary

_SHA = "a" * 40
_HEAD = "b" * 40
_NOW = datetime(2026, 10, 25, tzinfo=UTC)


def descriptor() -> RepositoryDescriptor:
    return RepositoryDescriptor(
        repository_id=RepositoryId("repo_" + "c" * 32),
        canonical_location=RepositoryLocation(Path(".")),
        name="linejs",
        default_branch="main",
        registered_at=_NOW,
    )


def pull_payload() -> dict[str, object]:
    return {
        "number": 12,
        "title": "Refactor token refresh",
        "state": "open",
        "html_url": "https://github.com/evex-dev/linejs/pull/12",
        "head": {"sha": _HEAD},
        "base": {"sha": _SHA},
        "user": {"login": "octocat"},
        "body": "Updates refresh handling.",
        "created_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-21T00:00:00Z",
        "merged": False,
        "additions": 12,
        "deletions": 3,
        "changed_files": 2,
    }


@pytest.mark.asyncio
async def test_adapter_reads_pull_request_issues_and_diff() -> None:
    """The REST adapter maps bounded JSON and diff responses to domain models."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls/12"):
            if "diff" in request.headers.get("accept", ""):
                return httpx.Response(200, text="diff --git a/x b/x\n+hello\n")
            return httpx.Response(200, json=pull_payload())
        if request.url.path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 5,
                        "title": "Token bug",
                        "state": "open",
                        "html_url": "https://github.com/evex-dev/linejs/issues/5",
                        "user": {"login": "alice"},
                        "body": "refresh fails",
                        "labels": [{"name": "bug"}],
                    },
                    {
                        "number": 6,
                        "title": "PR mirror",
                        "state": "open",
                        "html_url": "https://github.com/evex-dev/linejs/pull/6",
                        "pull_request": {"url": "https://api.github.com/pulls/6"},
                    },
                ],
            )
        return httpx.Response(404)

    settings = GitHubSettings(enabled=True, token="token")
    client = GitHubRestClient(settings, transport=httpx.MockTransport(handler))
    try:
        ref = GitHubRepositoryRef.parse("evex-dev/linejs")
        pull = await client.get_pull_request(ref, 12)
        issues = await client.list_issues(ref, state="open", limit=10)
        diff = await client.get_pull_request_diff(ref, 12)
    finally:
        await client.aclose()

    assert pull.head_sha == _HEAD
    assert pull.changed_files == 2
    assert [issue.number for issue in issues] == [5]
    assert issues[0].labels == ("bug",)
    assert diff.head_sha == _HEAD
    assert "+hello" in diff.diff


@pytest.mark.asyncio
async def test_adapter_reports_safe_not_found_and_auth_errors() -> None:
    settings = GitHubSettings(enabled=True, token="token")

    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    ref = GitHubRepositoryRef.parse("evex-dev/linejs")
    for handler, expected in (
        (not_found, GitHubNotFoundError),
        (unauthorized, GitHubAuthenticationError),
    ):
        client = GitHubRestClient(settings, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(expected):
                await client.get_pull_request(ref, 12)
        finally:
            await client.aclose()


class FakeProvider:
    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        assert repository_id == descriptor().repository_id
        return descriptor()


class FakeRegistry:
    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return (descriptor(),)

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor | None:
        return descriptor() if repository_id == descriptor().repository_id else None


class FakeIndex:
    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary | None:
        return IndexSnapshotSummary(
            repository_id=repository_id,
            commit_sha=_SHA,
            generation_id="gen_" + "d" * 32,
            generation_number=1,
            status=IndexGenerationStatus.ACTIVE,
        )


class FakeGitHub:
    async def get_pull_request(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestInfo:
        assert repository.full_name == "evex-dev/linejs"
        assert number == 12
        return PullRequestInfo(
            number=12,
            title="Refactor token refresh",
            state="open",
            url="https://github.com/evex-dev/linejs/pull/12",
            head_sha=_HEAD,
            base_sha=_SHA,
            author="octocat",
            body="Updates refresh handling.",
            created_at=_NOW,
            updated_at=_NOW,
            merged=False,
            additions=12,
            deletions=3,
            changed_files=2,
        )

    async def get_pull_request_diff(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestDiff:
        return PullRequestDiff(
            number=12,
            head_sha=_HEAD,
            url="https://github.com/evex-dev/linejs/pull/12/files",
            diff="diff --git a/x b/x\n+hello\n",
        )

    async def list_issues(
        self, repository: GitHubRepositoryRef, *, state: str, limit: int
    ) -> tuple[IssueInfo, ...]:
        assert state == "open"
        return (
            IssueInfo(
                number=5,
                title="Token bug",
                state="open",
                url="https://github.com/evex-dev/linejs/issues/5",
                author="alice",
                body="refresh fails",
                labels=("bug",),
                created_at=_NOW,
                updated_at=_NOW,
            ),
        )

    async def get_repository(self, repository: GitHubRepositoryRef) -> GitHubRepositoryInfo:
        return GitHubRepositoryInfo(
            full_name=repository.full_name,
            description=None,
            default_branch="main",
            url="https://github.com/evex-dev/linejs",
            private=False,
        )

    async def aclose(self) -> None:
        return None


def services() -> ToolServices:
    return ToolServices(
        repository=FakeProvider(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        index=FakeIndex(),  # type: ignore[arg-type]
        github=FakeGitHub(),
        default_github_repository="evex-dev/linejs",
    )


@pytest.mark.asyncio
async def test_pull_request_tool_returns_external_evidence() -> None:
    result = await get_pull_request(
        services(),
        ToolContext(request_id="req_github_1"),
        GetPullRequestInput(number="12"),
    )
    evidence = result.evidence[0]
    assert evidence.kind is EvidenceKind.PULL_REQUEST
    assert evidence.external_url == "https://github.com/evex-dev/linejs/pull/12"
    assert evidence.commit_sha == _HEAD
    assert evidence.path.as_posix() == "github/evex-dev/linejs/pull/12.md"
    assert "Refactor token refresh" in evidence.excerpt
    payload = json.loads(json.dumps(result.payload))
    assert payload["pull_request"]["number"] == 12


@pytest.mark.asyncio
async def test_diff_and_issue_tools_return_external_evidence() -> None:
    diff = await get_pull_request_diff(
        services(),
        ToolContext(request_id="req_github_2"),
        GetPullRequestDiffInput(number="12"),
    )
    assert diff.evidence[0].external_url.endswith("/files")
    assert "+hello" in diff.evidence[0].excerpt

    issues = await list_issues(
        services(),
        ToolContext(request_id="req_github_3"),
        ListIssuesInput(state="open", limit=10),
    )
    assert issues.evidence[0].kind is EvidenceKind.ISSUE
    assert issues.evidence[0].external_url.endswith("/issues/5")
    assert "Token bug" in issues.evidence[0].excerpt
