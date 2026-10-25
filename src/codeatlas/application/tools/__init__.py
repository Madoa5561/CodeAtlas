"""M4 retrieval tools: strict schemas, catalog, registry, and validated executor.

Composition root usage::

    index = SqliteIndexQuery(settings.database.url)
    executor = build_tool_executor(
        repository=repository_provider,
        registry=repository_registry,
        index=index,
    )
    definitions = executor.catalog()
    outcome = await executor.execute(canonical_call, tool_context)
"""

from __future__ import annotations

from codeatlas.application.tools.catalog import TOOL_SPECS, build_tool_registry
from codeatlas.application.tools.executor import ToolExecutionPolicy, ToolExecutor
from codeatlas.application.tools.registry import ToolRegistry
from codeatlas.application.tools.resolver import ToolIndexPort, ToolServices
from codeatlas.application.tools.schemas import ToolResult
from codeatlas.domain.github_ports import GitHubReadOnlyPort
from codeatlas.domain.repository_ports import RepositoryProviderPort, RepositoryRegistryPort
from codeatlas.domain.retrieval_models import RetrievalLimits

__all__ = [
    "TOOL_SPECS",
    "ToolExecutionPolicy",
    "ToolExecutor",
    "ToolIndexPort",
    "ToolRegistry",
    "ToolResult",
    "ToolServices",
    "build_tool_executor",
    "build_tool_registry",
]


def build_tool_executor(
    *,
    repository: RepositoryProviderPort,
    registry: RepositoryRegistryPort,
    index: ToolIndexPort,
    limits: RetrievalLimits | None = None,
    policy: ToolExecutionPolicy | None = None,
    tool_registry: ToolRegistry | None = None,
    github: GitHubReadOnlyPort | None = None,
    default_github_repository: str | None = None,
) -> ToolExecutor:
    """Construct the validated tool executor used by the Retriever.

    ``index`` is normally a single ``SqliteIndexQuery`` instance; the same object
    also satisfies the additive read-only query capabilities the tools need.
    """
    services = ToolServices(
        repository=repository,
        registry=registry,
        index=index,
        limits=limits or RetrievalLimits(),
        github=github,
        default_github_repository=default_github_repository,
    )
    return ToolExecutor(tool_registry or build_tool_registry(), services, policy)
