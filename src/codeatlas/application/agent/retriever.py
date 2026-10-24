"""Bounded execution of an investigation plan through the tool executor."""

from __future__ import annotations

import asyncio
import logging

from codeatlas.domain.agent_models import (
    AgentLimits,
    CanonicalToolCall,
    EvidenceCandidate,
    InvestigationPlan,
    QueryContext,
    RetrievalResult,
    ToolContext,
    ToolDefinition,
    ToolOutcome,
)
from codeatlas.domain.agent_ports import ToolExecutorPort
from codeatlas.domain.repository_models import RepositoryId

logger = logging.getLogger(__name__)

_MAX_WARNINGS = 20
_MAX_CANDIDATES = 50


class ToolPlanRetriever:
    """Execute plan steps through the validated tool executor and normalize evidence."""

    def __init__(self, *, executor: ToolExecutorPort) -> None:
        self._executor = executor

    def catalog(self) -> tuple[ToolDefinition, ...]:
        """Return the tool catalog used for planning."""
        return self._executor.catalog()

    async def retrieve(
        self,
        plan: InvestigationPlan,
        context: QueryContext,
        tool_context: ToolContext,
        limits: AgentLimits,
    ) -> RetrievalResult:
        """Execute bounded plan steps and return deduplicated, scoped evidence."""
        del context
        catalog_names = {definition.name for definition in self._executor.catalog()}
        candidates: list[EvidenceCandidate] = []
        seen_ids: set[str] = set()
        search_methods: list[str] = []
        warnings: list[str] = []
        tool_calls_used = 0
        max_steps = min(len(plan.steps), limits.max_plan_steps, limits.max_tool_calls)
        allowed_repositories = set(tool_context.allowed_repository_ids)
        for index, step in enumerate(plan.steps[:max_steps], start=1):
            if step.tool_name not in catalog_names:
                _warn(warnings, f"tool unavailable: {step.tool_name}")
                continue
            if tool_calls_used >= limits.max_tool_calls:
                break
            call = CanonicalToolCall(
                call_id=f"call_{index:02d}",
                tool_name=step.tool_name,
                arguments_json=step.arguments_json,
            )
            tool_calls_used += 1
            if step.tool_name not in search_methods:
                search_methods.append(step.tool_name)
            try:
                outcome = await self._executor.execute(call, tool_context)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - defensive boundary
                logger.warning(
                    "agent_tool_call_raised",
                    extra={"tool_name": step.tool_name, "error_type": type(error).__name__},
                )
                _warn(warnings, f"{step.tool_name}: execution failed")
                continue
            if not outcome.ok:
                category = (
                    outcome.error_category.value if outcome.error_category is not None else "error"
                )
                _warn(warnings, f"{step.tool_name}: {category}")
                continue
            if outcome.truncated:
                _warn(warnings, f"{step.tool_name}: result truncated")
            self._collect(
                outcome,
                candidates=candidates,
                seen_ids=seen_ids,
                allowed_repositories=allowed_repositories,
                limit=min(limits.max_evidence * 4, _MAX_CANDIDATES),
                warnings=warnings,
            )
        return RetrievalResult(
            candidates=tuple(candidates),
            search_methods=tuple(search_methods),
            tool_calls_used=tool_calls_used,
            warnings=tuple(warnings[:_MAX_WARNINGS]),
        )

    @staticmethod
    def _collect(
        outcome: ToolOutcome,
        *,
        candidates: list[EvidenceCandidate],
        seen_ids: set[str],
        allowed_repositories: set[RepositoryId],
        limit: int,
        warnings: list[str],
    ) -> None:
        for evidence in outcome.evidence:
            if len(candidates) >= limit:
                break
            if evidence.evidence_id in seen_ids:
                continue
            if allowed_repositories and evidence.repository_id not in allowed_repositories:
                _warn(warnings, f"{outcome.tool_name}: evidence outside authorized scope")
                continue
            seen_ids.add(evidence.evidence_id)
            candidates.append(evidence)


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < _MAX_WARNINGS * 2:
        warnings.append(message)
