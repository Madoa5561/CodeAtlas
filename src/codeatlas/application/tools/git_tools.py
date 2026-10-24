"""Bounded Git history retrieval tools; reads use immutable commit objects only."""

from __future__ import annotations

from codeatlas.application.tools.evidence import (
    commit_evidence,
    diff_evidence,
    span_evidence,
)
from codeatlas.application.tools.resolver import (
    ToolServices,
    bounded_text,
    clamp_limit,
    commit_payload,
    diff_payload,
    iso_timestamp,
    parse_path,
    parse_timestamp,
    resolve_repository,
    resolve_revision,
)
from codeatlas.application.tools.schemas import (
    CompareRefsInput,
    GetBlameInput,
    GetCommitInput,
    GetDiffInput,
    GetFileHistoryInput,
    SearchCommitsInput,
    ToolResult,
)
from codeatlas.domain.agent_models import ToolContext
from codeatlas.domain.repository_models import CommitInfo, RepositoryPath, RevisionRef


async def get_commit(
    services: ToolServices, context: ToolContext, arguments: GetCommitInput
) -> ToolResult:
    """Return metadata for one immutable commit."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    commit = await services.repository.get_commit(revision)
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "resolved_commit_sha": revision.commit_sha,
            "commit": commit_payload(commit, max_text_chars=4_000),
        },
        evidence=(commit_evidence(descriptor, commit, retrieval_method="git_commit"),),
    )


def _commit_filter_payload(commit: CommitInfo) -> dict[str, object]:
    return commit_payload(commit, max_text_chars=2_000)


async def search_commits(
    services: ToolServices, context: ToolContext, arguments: SearchCommitsInput
) -> ToolResult:
    """Search bounded commit metadata with optional author, date, and path filters."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    limit = clamp_limit(arguments.limit, 100)
    path = None if arguments.path is None else parse_path(arguments.path, allow_root=False)
    since = None if arguments.since is None else parse_timestamp(arguments.since, "since")
    until = None if arguments.until is None else parse_timestamp(arguments.until, "until")
    commits = await services.repository.search_commits(
        descriptor.repository_id,
        arguments.query,
        revision=RevisionRef(revision.commit_sha),
        author=arguments.author,
        since=since,
        until=until,
        path=path,
        max_results=limit,
    )
    evidence_path = path or RepositoryPath.root()
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "query": arguments.query,
            "commits": [_commit_filter_payload(commit) for commit in commits],
            "count": len(commits),
        },
        evidence=tuple(
            commit_evidence(
                descriptor,
                commit,
                retrieval_method="git_commit_search",
                path=evidence_path,
            )
            for commit in commits
        ),
        truncated=len(commits) >= limit,
    )


async def get_file_history(
    services: ToolServices, context: ToolContext, arguments: GetFileHistoryInput
) -> ToolResult:
    """Return bounded commit history for one path, following renames by policy."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    path = parse_path(arguments.path, allow_root=False)
    limit = clamp_limit(arguments.limit, 100)
    entries = await services.repository.get_file_history(revision, path, max_results=limit)
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "path": path.as_posix(),
            "history": [
                {
                    "commit_sha": entry.commit.commit_sha,
                    "path": entry.path.as_posix(),
                    "subject": entry.commit.subject,
                    "authored_at": iso_timestamp(entry.commit.authored_at),
                }
                for entry in entries
            ],
            "count": len(entries),
        },
        evidence=tuple(
            commit_evidence(
                descriptor,
                entry.commit,
                retrieval_method="git_file_history",
                path=entry.path,
            )
            for entry in entries
        ),
        truncated=len(entries) >= limit,
    )


async def get_blame(
    services: ToolServices, context: ToolContext, arguments: GetBlameInput
) -> ToolResult:
    """Return commit-pinned line attribution ranges for one path."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    path = parse_path(arguments.path, allow_root=False)
    limit = clamp_limit(arguments.limit, 200)
    entries = await services.repository.get_blame(revision, path)
    if arguments.start_line is not None:
        entries = tuple(
            entry
            for entry in entries
            if entry.start_line + entry.line_count - 1 >= arguments.start_line
        )
    if arguments.end_line is not None:
        entries = tuple(entry for entry in entries if entry.start_line <= arguments.end_line)
    selected = entries[:limit]
    evidence_items = []
    ranges: list[dict[str, object]] = []
    for entry in selected:
        joined = "\n".join(entry.content)
        excerpt, _ = bounded_text(joined, services.limits.max_excerpt_chars)
        end_line = entry.start_line + max(1, entry.line_count) - 1
        if excerpt.strip():
            evidence_items.append(
                span_evidence(
                    descriptor,
                    entry.commit_sha,
                    path,
                    entry.start_line,
                    end_line,
                    excerpt,
                    retrieval_method="git_blame",
                )
            )
        ranges.append(
            {
                "commit_sha": entry.commit_sha,
                "start_line": entry.start_line,
                "line_count": entry.line_count,
                "end_line": end_line,
                "author_name": entry.author_name,
                "authored_at": iso_timestamp(entry.authored_at),
                "content": excerpt,
            }
        )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "resolved_commit_sha": revision.commit_sha,
            "path": path.as_posix(),
            "ranges": ranges,
            "count": len(ranges),
        },
        evidence=tuple(evidence_items),
        truncated=len(entries) > limit,
    )


async def compare_refs(
    services: ToolServices, context: ToolContext, arguments: CompareRefsInput
) -> ToolResult:
    """Compare two revisions of the same repository with bounded safe diffs."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    base = await resolve_revision(services, context, descriptor, arguments.base)
    head = await resolve_revision(services, context, descriptor, arguments.head)
    diff = await services.repository.compare_refs(base, head)
    payload, truncated = diff_payload(diff, max_text_chars=services.limits.max_content_chars)
    evidence = diff_evidence(descriptor, head.commit_sha, diff.text, retrieval_method="git_compare")
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "base_sha": base.commit_sha,
            "head_sha": head.commit_sha,
            "diff": payload,
        },
        evidence=(evidence,) if evidence is not None else (),
        truncated=truncated,
    )


async def get_diff(
    services: ToolServices, context: ToolContext, arguments: GetDiffInput
) -> ToolResult:
    """Return the bounded diff of one commit against its first parent."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    diff = await services.repository.get_diff(revision)
    payload, truncated = diff_payload(diff, max_text_chars=services.limits.max_content_chars)
    evidence = diff_evidence(
        descriptor, revision.commit_sha, diff.text, retrieval_method="git_diff"
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "diff": payload,
        },
        evidence=(evidence,) if evidence is not None else (),
        truncated=truncated,
    )
