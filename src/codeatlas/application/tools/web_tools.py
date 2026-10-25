"""Safe outbound HTTP fetch tool."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from codeatlas.application.tools.resolver import (
    ToolServices,
    require_snapshot,
    resolve_repository,
)
from codeatlas.application.tools.schemas import FetchHttpInput, ToolResult
from codeatlas.domain.agent_models import EvidenceCandidate, EvidenceKind, ToolContext
from codeatlas.domain.errors import NotFoundError
from codeatlas.domain.repository_models import RepositoryPath

_MAX_EXCERPT = 7_500


async def fetch_http(
    services: ToolServices, context: ToolContext, arguments: FetchHttpInput
) -> ToolResult:
    """Fetch one allowlisted public document with SSRF controls."""
    if services.http is None:
        raise NotFoundError("The HTTP fetch tool is not enabled.")
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, _generation_id = await require_snapshot(services, descriptor)
    document = await services.http.fetch(arguments.url, method=arguments.method)
    if document.text:
        excerpt = document.text[:_MAX_EXCERPT]
    else:
        excerpt = (
            f"HTTP {arguments.method} {document.url} returned "
            f"{document.status} ({document.content_type})."
        )
    lines = max(1, len(excerpt.splitlines()))
    digest = hashlib.sha256(document.url.encode("utf-8")).hexdigest()[:16]
    host = document.url.split("://", 1)[-1].split("/", 1)[0]
    path = RepositoryPath(PurePosixPath(f"web/{host}/{digest}.md"))
    evidence = EvidenceCandidate.create(
        kind=EvidenceKind.FILE_EXCERPT,
        repository_id=descriptor.repository_id,
        commit_sha=snapshot.commit_sha,
        path=path,
        start_line=1,
        end_line=lines,
        excerpt=excerpt,
        retrieval_method="http_fetch",
        external_url=document.url,
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "url": document.url,
            "status": document.status,
            "content_type": document.content_type,
            "title": document.title,
            "text": document.text,
            "truncated": document.truncated,
        },
        evidence=(evidence,),
        truncated=document.truncated,
    )
