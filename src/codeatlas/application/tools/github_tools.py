"""Read-only GitHub pull-request and issue retrieval tools."""

from __future__ import annotations

from pathlib import PurePosixPath

from codeatlas.application.tools.resolver import (
    ToolServices,
    require_snapshot,
    resolve_repository,
)
from codeatlas.application.tools.schemas import (
    GetPullRequestDiffInput,
    GetPullRequestInput,
    ListIssuesInput,
    ToolResult,
)
from codeatlas.domain.agent_models import EvidenceCandidate, EvidenceKind, ToolContext
from codeatlas.domain.errors import InputValidationError, NotFoundError
from codeatlas.domain.github_models import GitHubRepositoryRef, IssueInfo, PullRequestInfo
from codeatlas.domain.github_ports import GitHubReadOnlyPort
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryPath

_MAX_EXCERPT = 7_500


def _client(services: ToolServices) -> GitHubReadOnlyPort:
    if services.github is None:
        raise NotFoundError("GitHub integration is not enabled.")
    return services.github


def _repository_ref(services: ToolServices, explicit: str | None) -> GitHubRepositoryRef:
    value = explicit or services.default_github_repository
    if value is None:
        raise InputValidationError(
            "A GitHub repository is required, or configure github.default_repository."
        )
    try:
        return GitHubRepositoryRef.parse(value)
    except ValueError as error:
        raise InputValidationError(
            "The GitHub repository is invalid.", internal_cause=error
        ) from error


def _evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    *,
    kind: EvidenceKind,
    slug: str,
    path_suffix: str,
    excerpt: str,
    method: str,
    external_url: str,
) -> EvidenceCandidate:
    path = RepositoryPath(PurePosixPath(f"github/{slug}/{path_suffix}"))
    bounded = excerpt[:_MAX_EXCERPT]
    lines = max(1, len(bounded.splitlines()))
    return EvidenceCandidate.create(
        kind=kind,
        repository_id=descriptor.repository_id,
        commit_sha=commit_sha,
        path=path,
        start_line=1,
        end_line=lines,
        excerpt=bounded,
        retrieval_method=method,
        external_url=external_url,
    )


def _pull_request_payload(pull: PullRequestInfo) -> dict[str, object]:
    return {
        "number": pull.number,
        "title": pull.title,
        "state": pull.state,
        "url": pull.url,
        "head_sha": pull.head_sha,
        "base_sha": pull.base_sha,
        "author": pull.author,
        "body": pull.body,
        "created_at": pull.created_at.isoformat() if pull.created_at else None,
        "updated_at": pull.updated_at.isoformat() if pull.updated_at else None,
        "merged": pull.merged,
        "additions": pull.additions,
        "deletions": pull.deletions,
        "changed_files": pull.changed_files,
    }


def _issue_payload(issue: IssueInfo) -> dict[str, object]:
    return {
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
        "url": issue.url,
        "author": issue.author,
        "body": issue.body,
        "labels": list(issue.labels),
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
    }


async def get_pull_request(
    services: ToolServices, context: ToolContext, arguments: GetPullRequestInput
) -> ToolResult:
    """Return one pull request as external evidence plus a structured payload."""
    client = _client(services)
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, _generation_id = await require_snapshot(services, descriptor)
    number = int(arguments.number)
    pull = await client.get_pull_request(
        _repository_ref(services, arguments.repository),
        number,
    )
    excerpt = (
        f"Pull request #{pull.number}: {pull.title}\n"
        f"State: {pull.state}{' (merged)' if pull.merged else ''}\n"
        f"URL: {pull.url}\nHead SHA: {pull.head_sha}\n"
        f"Changes: +{pull.additions}/-{pull.deletions} in {pull.changed_files} files\n\n"
        f"{pull.body}"
    )
    slug = _repository_ref(services, arguments.repository).full_name
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "pull_request": _pull_request_payload(pull),
        },
        evidence=(
            _evidence(
                descriptor,
                pull.head_sha,
                kind=EvidenceKind.PULL_REQUEST,
                slug=slug,
                path_suffix=f"pull/{pull.number}.md",
                excerpt=excerpt,
                method="github_pr",
                external_url=pull.url,
            ),
        ),
    )


async def get_pull_request_diff(
    services: ToolServices, context: ToolContext, arguments: GetPullRequestDiffInput
) -> ToolResult:
    """Return the bounded pull request diff as external evidence."""
    client = _client(services)
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, _generation_id = await require_snapshot(services, descriptor)
    ref = _repository_ref(services, arguments.repository)
    number = int(arguments.number)
    pull_diff = await client.get_pull_request_diff(ref, number)
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "number": pull_diff.number,
            "head_sha": pull_diff.head_sha,
            "url": pull_diff.url,
            "diff": pull_diff.diff,
            "truncated": len(pull_diff.diff) >= 200_000,
        },
        evidence=(
            _evidence(
                descriptor,
                pull_diff.head_sha,
                kind=EvidenceKind.PULL_REQUEST,
                slug=ref.full_name,
                path_suffix=f"pull/{number}/diff.txt",
                excerpt=pull_diff.diff,
                method="github_pr_diff",
                external_url=pull_diff.url,
            ),
        ),
        truncated=len(pull_diff.diff) >= 200_000,
    )


async def list_issues(
    services: ToolServices, context: ToolContext, arguments: ListIssuesInput
) -> ToolResult:
    """List bounded issues as external evidence."""
    client = _client(services)
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, _generation_id = await require_snapshot(services, descriptor)
    ref = _repository_ref(services, arguments.repository)
    issues = await client.list_issues(ref, state=arguments.state, limit=arguments.limit)
    evidence = tuple(
        _evidence(
            descriptor,
            snapshot.commit_sha,
            kind=EvidenceKind.ISSUE,
            slug=ref.full_name,
            path_suffix=f"issues/{issue.number}.md",
            excerpt=(
                f"Issue #{issue.number}: {issue.title}\n"
                f"State: {issue.state}\nURL: {issue.url}\n"
                f"Labels: {', '.join(issue.labels) or '-'}\n\n{issue.body}"
            ),
            method="github_issue",
            external_url=issue.url,
        )
        for issue in issues
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "github_repository": ref.full_name,
            "state": arguments.state,
            "issues": [_issue_payload(issue) for issue in issues],
            "count": len(issues),
        },
        evidence=evidence,
        truncated=len(issues) >= arguments.limit,
    )
