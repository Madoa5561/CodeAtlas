"""Catalog, tree, file, and text-search retrieval tools."""

from __future__ import annotations

from codeatlas.application.tools.evidence import span_evidence, text_evidence, tree_evidence
from codeatlas.application.tools.resolver import (
    ToolServices,
    authorized_repositories,
    bounded_text,
    clamp_limit,
    parse_path,
    repository_payload,
    require_snapshot,
    resolve_repository,
    resolve_revision,
)
from codeatlas.application.tools.schemas import (
    GetRepositoryInput,
    ListBranchesInput,
    ListDirectoryInput,
    ListRepositoriesInput,
    ListTagsInput,
    ReadFileInput,
    SearchCodeInput,
    SearchTextInput,
    ToolResult,
)
from codeatlas.domain.agent_models import ToolContext
from codeatlas.domain.errors import InputValidationError
from codeatlas.domain.repository_models import (
    FileContent,
    RepositoryPath,
    TextSearchQuery,
)


async def list_repositories(
    services: ToolServices, context: ToolContext, arguments: ListRepositoriesInput
) -> ToolResult:
    """List only the repositories authorized for this request."""
    repositories = authorized_repositories(services, context)
    return ToolResult(
        {
            "repositories": [repository_payload(item) for item in repositories],
            "count": len(repositories),
        }
    )


async def get_repository(
    services: ToolServices, context: ToolContext, arguments: GetRepositoryInput
) -> ToolResult:
    """Return one registered repository descriptor without filesystem paths."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    return ToolResult({"repository": repository_payload(descriptor)})


async def list_branches(
    services: ToolServices, context: ToolContext, arguments: ListBranchesInput
) -> ToolResult:
    """List branches resolved to immutable commit SHAs."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    limit = clamp_limit(arguments.limit, 200)
    revisions = await services.repository.list_branches(descriptor.repository_id)
    selected = revisions[:limit]
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "branches": [
                {"name": item.requested.value, "commit_sha": item.commit_sha} for item in selected
            ],
            "count": len(selected),
            "total": len(revisions),
        },
        truncated=len(revisions) > limit,
    )


async def list_tags(
    services: ToolServices, context: ToolContext, arguments: ListTagsInput
) -> ToolResult:
    """List tags peeled and resolved to immutable commit SHAs."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    limit = clamp_limit(arguments.limit, 200)
    revisions = await services.repository.list_tags(descriptor.repository_id)
    selected = revisions[:limit]
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "tags": [
                {"name": item.requested.value, "commit_sha": item.commit_sha} for item in selected
            ],
            "count": len(selected),
            "total": len(revisions),
        },
        truncated=len(revisions) > limit,
    )


async def list_directory(
    services: ToolServices, context: ToolContext, arguments: ListDirectoryInput
) -> ToolResult:
    """List one commit tree directory from commit objects, never the working tree."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    path = parse_path(arguments.path, allow_root=True)
    limit = clamp_limit(arguments.limit, 200)
    entries = await services.repository.list_directory(revision, path)
    selected = entries[:limit]
    evidence = tuple(
        tree_evidence(
            descriptor,
            revision.commit_sha,
            entry.path,
            retrieval_method="git_tree",
        )
        for entry in selected
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "path": path.as_posix(),
            "entries": [
                {
                    "name": entry.name,
                    "path": entry.path.as_posix(),
                    "entry_type": entry.entry_type.value,
                    "size": entry.size,
                    "unsupported_reason": entry.unsupported_reason,
                }
                for entry in selected
            ],
            "count": len(selected),
            "total": len(entries),
        },
        evidence=evidence,
        truncated=len(entries) > limit,
    )


def _select_content_range(
    content: FileContent,
    start_line: int | None,
    end_line: int | None,
) -> tuple[str, int, int]:
    lines = content.content.splitlines()
    total = max(content.line_count, len(lines))
    start = 1 if start_line is None else start_line
    end = total if end_line is None else end_line
    if total and start > total:
        raise InputValidationError("The requested start_line is outside the file.")
    end = max(start, min(end, total)) if total else 0
    selected = lines[max(0, start - 1) : end]
    return "\n".join(selected), start, end


async def read_file(
    services: ToolServices, context: ToolContext, arguments: ReadFileInput
) -> ToolResult:
    """Read a bounded line range from a commit-pinned, policy-validated text file."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    path = parse_path(arguments.path, allow_root=False)
    content = await services.repository.read_file(revision, path)
    text, start, end = _select_content_range(content, arguments.start_line, arguments.end_line)
    maximum = min(
        arguments.max_chars or services.limits.max_content_chars,
        services.limits.max_content_chars,
    )
    bounded, truncated = bounded_text(text, maximum)
    evidence_text, _ = bounded_text(text, 2_000)
    evidence = (
        span_evidence(
            descriptor,
            revision.commit_sha,
            path,
            start,
            end,
            evidence_text,
            retrieval_method="read_file",
        ),
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "path": path.as_posix(),
            "encoding": content.encoding,
            "byte_size": content.byte_size,
            "line_count": content.line_count,
            "start_line": start,
            "end_line": end,
            "content": bounded,
        },
        evidence=evidence,
        truncated=content.truncated or truncated or start > 1 or end < max(content.line_count, 1),
    )


def _hit_payload(
    path: RepositoryPath, line: int, excerpt: str, start_column: int, end_column: int
) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "line": line,
        "excerpt": excerpt,
        "start_column": start_column,
        "end_column": end_column,
    }


async def search_text(
    services: ToolServices, context: ToolContext, arguments: SearchTextInput
) -> ToolResult:
    """Literal text search over a SHA-pinned repository snapshot."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    revision = await resolve_revision(services, context, descriptor, arguments.revision)
    path_prefix = parse_path(arguments.path_prefix, allow_root=True)
    limit = clamp_limit(arguments.limit, 100)
    query = TextSearchQuery(
        pattern=arguments.query,
        case_sensitive=arguments.case_sensitive,
        path_prefix=path_prefix,
        max_results=limit,
    )
    hits = await services.repository.search_text(revision, query)
    evidence = tuple(
        text_evidence(
            descriptor,
            revision.commit_sha,
            hit.path,
            hit.line,
            hit.excerpt,
            retrieval_method="git_text_search",
        )
        for hit in hits
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": revision.commit_sha,
            "case_sensitive": arguments.case_sensitive,
            "hits": [
                _hit_payload(hit.path, hit.line, hit.excerpt, hit.match_start, hit.match_end)
                for hit in hits
            ],
            "count": len(hits),
        },
        evidence=evidence,
        truncated=len(hits) >= limit,
    )


async def search_code(
    services: ToolServices, context: ToolContext, arguments: SearchCodeInput
) -> ToolResult:
    """Literal text search over safe content stored in the active index."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    hits = await services.index.search_text(
        generation_id,
        pattern=arguments.query,
        case_sensitive=arguments.case_sensitive,
        limit=clamp_limit(arguments.limit, 100),
    )
    evidence = tuple(
        text_evidence(
            descriptor,
            snapshot.commit_sha,
            hit.path,
            hit.line,
            hit.excerpt,
            retrieval_method="index_text_search",
        )
        for hit in hits
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "case_sensitive": arguments.case_sensitive,
            "hits": [
                _hit_payload(hit.path, hit.line, hit.excerpt, hit.start_column, hit.end_column)
                for hit in hits
            ],
            "count": len(hits),
        },
        evidence=evidence,
        truncated=len(hits) >= clamp_limit(arguments.limit, 100),
    )
