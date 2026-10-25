"""PR review analysis tool tests with fake GitHub and index collaborators."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from codeatlas.application.tools.analysis_tools import review_pull_request
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import ReviewPullRequestInput
from codeatlas.domain.agent_models import EvidenceKind, ToolContext
from codeatlas.domain.github_models import (
    GitHubRepositoryRef,
    PullRequestDiff,
    PullRequestInfo,
)
from codeatlas.domain.indexing_models import (
    IndexedFileId,
    IndexedSymbol,
    IndexGenerationStatus,
    SourceSpan,
    SymbolId,
    SymbolKind,
)
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
    RepositoryPath,
)
from codeatlas.domain.retrieval_models import IndexSnapshotSummary

_REPO = RepositoryId("repo_" + "a" * 32)
_SHA = "b" * 40
_HEAD = "c" * 40
_NOW = datetime(2026, 10, 25, tzinfo=UTC)
_PATH = RepositoryPath(PurePosixPath("src/client.ts"))


def descriptor() -> RepositoryDescriptor:
    return RepositoryDescriptor(
        repository_id=_REPO,
        canonical_location=RepositoryLocation(Path(".")),
        name="linejs",
        default_branch="main",
        registered_at=_NOW,
    )


class FakeProvider:
    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        return descriptor()


class FakeRegistry:
    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return (descriptor(),)

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor | None:
        return descriptor()


class FakeIndex:
    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary:
        return IndexSnapshotSummary(
            repository_id=repository_id,
            commit_sha=_SHA,
            generation_id="gen_" + "d" * 32,
            generation_number=1,
            status=IndexGenerationStatus.ACTIVE,
        )

    async def list_symbols(
        self, generation_id: str, *, path: RepositoryPath | None, limit: int
    ) -> tuple[IndexedSymbol, ...]:
        return (
            IndexedSymbol(
                symbol_id=SymbolId.derive(
                    _REPO,
                    _SHA,
                    "typescript",
                    _PATH,
                    SymbolKind.CLASS,
                    "Client",
                    "class Client",
                    _span(),
                ),
                generation_id=generation_id,
                file_id=IndexedFileId.derive(_REPO, _PATH),
                kind=SymbolKind.CLASS,
                name="Client",
                qualified_name="Client",
                signature="class Client",
                span=_span(),
                parent_symbol_id=None,
                exported=True,
                has_documentation=False,
                parser_id="tree-sitter",
                parser_version="1.0.0",
                grammar_version="1.0.0",
            ),
        )

    async def find_relations_filtered(
        self,
        generation_id: str,
        *,
        raw_target: str | None = None,
        limit: int = 100,
    ) -> tuple[object, ...]:
        return ()


class FakeGitHub:
    async def get_pull_request(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestInfo:
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
            changed_files=1,
        )

    async def get_pull_request_diff(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestDiff:
        return PullRequestDiff(
            number=12,
            head_sha=_HEAD,
            url="https://github.com/evex-dev/linejs/pull/12/files",
            diff="diff --git a/src/client.ts b/src/client.ts\n+++ b/src/client.ts\n+hello\n",
        )


def _span() -> SourceSpan:
    return SourceSpan(0, 10, 1, 0, 1, 10)


@pytest.mark.asyncio
async def test_review_pull_request_combines_diff_and_impact() -> None:
    services = ToolServices(
        repository=FakeProvider(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        index=FakeIndex(),  # type: ignore[arg-type]
        github=FakeGitHub(),
        default_github_repository="evex-dev/linejs",
    )
    result = await review_pull_request(
        services,
        ToolContext(request_id="req_review_1"),
        ReviewPullRequestInput(number="12"),
    )
    assert result.evidence[0].kind is EvidenceKind.PULL_REQUEST
    assert result.evidence[0].external_url == "https://github.com/evex-dev/linejs/pull/12"
    assert "src/client.ts" in result.evidence[0].excerpt
    assert result.payload["pull_request"]["number"] == 12
