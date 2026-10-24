"""Budgeted, cancellation-safe orchestration of the five-stage agent pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from codeatlas.application.agent._text import localized
from codeatlas.application.agent.composer import CitationComposer
from codeatlas.application.agent.diagnostics import (
    DegradedReason,
    ReasonerDiagnostics,
    ReasonerDiagnosticsSource,
)
from codeatlas.application.agent.planner import HeuristicPlanner
from codeatlas.application.agent.ranker import HeuristicEvidenceRanker
from codeatlas.application.agent.reasoner import GroundedReasoner
from codeatlas.application.agent.retriever import ToolPlanRetriever
from codeatlas.domain.agent_models import (
    AgentDiagnostics,
    AgentLimits,
    AgentOutcome,
    EvidenceCandidate,
    QueryContext,
    RankedEvidenceSet,
    ReasoningResult,
    RetrievalResult,
    ToolContext,
)
from codeatlas.domain.agent_ports import (
    EvidenceRankerPort,
    ModelProviderPort,
    PlannerPort,
    ReasonerPort,
    ResponseComposerPort,
    RetrieverPort,
    ToolExecutorPort,
)
from codeatlas.domain.errors import AgentError
from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Coordinate planning, retrieval, ranking, reasoning, and composition.

    The orchestrator owns only state, budgets, deadlines, replanning, and failure
    classification. It never raises raw exceptions to the application service.
    """

    def __init__(
        self,
        *,
        planner: PlannerPort,
        retriever: RetrieverPort,
        ranker: EvidenceRankerPort,
        reasoner: ReasonerPort,
        composer: ResponseComposerPort,
    ) -> None:
        self._planner = planner
        self._retriever = retriever
        self._ranker = ranker
        self._reasoner = reasoner
        self._composer = composer
        self._diagnostics_source: ReasonerDiagnosticsSource | None = (
            reasoner if isinstance(reasoner, ReasonerDiagnosticsSource) else None
        )

    @classmethod
    def default(
        cls,
        *,
        executor: ToolExecutorPort,
        provider: ModelProviderPort | None = None,
        fallback_without_model: bool = True,
    ) -> AgentOrchestrator:
        """Build the standard heuristic pipeline for a composition root."""
        return cls(
            planner=HeuristicPlanner(),
            retriever=ToolPlanRetriever(executor=executor),
            ranker=HeuristicEvidenceRanker(),
            reasoner=GroundedReasoner(
                provider=provider,
                fallback_without_model=fallback_without_model,
            ),
            composer=CitationComposer(),
        )

    async def run(self, context: QueryContext, limits: AgentLimits) -> AgentOutcome:
        """Execute the pipeline within its budget or return a safe failure."""
        try:
            async with asyncio.timeout(limits.deadline_seconds):
                return await self._run(context, limits)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("agent_deadline_exceeded")
            return self._failure(context, DegradedReason.DEADLINE_EXCEEDED)
        except AgentError as error:
            logger.warning("agent_safe_failure", extra={"error_code": error.code})
            return self._failure(
                context,
                DegradedReason.UNEXPECTED_ERROR,
                extra_reasons=self._reasoner_degraded_reasons(),
            )
        except Exception as error:
            logger.warning(
                "agent_unexpected_failure",
                extra={"error_type": type(error).__name__},
            )
            return self._failure(
                context,
                DegradedReason.UNEXPECTED_ERROR,
                extra_reasons=self._reasoner_degraded_reasons(),
            )

    def _reasoner_degraded_reasons(self) -> tuple[str, ...]:
        source = self._diagnostics_source
        if source is None:
            return ()
        return source.last_diagnostics.degraded_reasons

    async def _run(self, context: QueryContext, limits: AgentLimits) -> AgentOutcome:
        catalog = self._retriever.catalog()
        if not catalog:
            raise AgentError("No retrieval tools are available.")
        plan = await self._planner.plan(context, catalog, limits)
        tool_context = ToolContext(
            request_id=context.request_id,
            allowed_repository_ids=context.repository_scope,
        )
        accumulated: list[EvidenceCandidate] = []
        seen_evidence: set[str] = set()
        methods: list[str] = []
        executed: set[tuple[str, str]] = set()
        degraded: list[str] = []
        iterations = 0
        tool_calls = 0
        ranked = RankedEvidenceSet()
        reasoning: ReasoningResult | None = None
        while iterations < limits.max_iterations:
            iterations += 1
            remaining = limits.max_tool_calls - tool_calls
            if remaining <= 0:
                break
            round_limits = replace(
                limits,
                max_tool_calls=remaining,
                max_plan_steps=min(limits.max_plan_steps, remaining),
            )
            retrieval = await self._retriever.retrieve(plan, context, tool_context, round_limits)
            tool_calls += retrieval.tool_calls_used
            for method in retrieval.search_methods:
                if method not in methods:
                    methods.append(method)
            for candidate in retrieval.candidates:
                if candidate.evidence_id not in seen_evidence:
                    seen_evidence.add(candidate.evidence_id)
                    accumulated.append(candidate)
            executed.update((step.tool_name, step.arguments_json) for step in plan.steps)
            combined = RetrievalResult(
                candidates=tuple(accumulated),
                search_methods=tuple(methods),
                tool_calls_used=tool_calls,
            )
            ranked = await self._ranker.rank(context, combined, limits)
            reasoning = await self._reasoner.reason(context, ranked, limits)
            if not reasoning.gaps:
                break
            if iterations >= limits.max_iterations:
                degraded.append(DegradedReason.ITERATION_LIMIT.value)
                break
            if tool_calls >= limits.max_tool_calls:
                degraded.append(DegradedReason.TOOL_BUDGET_EXHAUSTED.value)
                break
            follow_up = await self._planner.replan(context, plan, reasoning.gaps, catalog, limits)
            fresh = [
                (step.tool_name, step.arguments_json)
                for step in follow_up.steps
                if (step.tool_name, step.arguments_json) not in executed
            ]
            if not fresh:
                break
            plan = follow_up
        if reasoning is None:
            reasoning = await self._reasoner.reason(context, ranked, limits)
        diagnostics_source = self._diagnostics_source
        reasoner_diagnostics = (
            diagnostics_source.last_diagnostics
            if diagnostics_source is not None
            else ReasonerDiagnostics()
        )
        if not ranked.evidence:
            degraded.append(DegradedReason.EMPTY_EVIDENCE.value)
        if reasoner_diagnostics.degraded_reasons:
            degraded.extend(reasoner_diagnostics.degraded_reasons)
        if not ranked.evidence and methods:
            methods_note = localized(
                context.locale,
                ja="試した検索方法: " + ", ".join(methods),
                en="Search methods tried: " + ", ".join(methods),
            )
            reasoning = replace(
                reasoning,
                uncertainties=(*reasoning.uncertainties, methods_note),
            )
        response = await self._composer.compose(
            context,
            reasoning,
            ranked,
            context.capabilities,
            limits,
        )
        if degraded and response.status is ResponseStatus.OK:
            response = replace(response, status=ResponseStatus.PARTIAL)
        if methods and response.search_methods != tuple(methods):
            response = replace(response, search_methods=tuple(methods))
        return AgentOutcome(
            response=response,
            diagnostics=AgentDiagnostics(
                iterations=iterations,
                tool_calls=tool_calls,
                evidence_ranked=len(ranked.evidence),
                search_methods=tuple(methods),
                model_id=reasoner_diagnostics.model_id,
                degraded_reasons=tuple(dict.fromkeys(degraded)),
            ),
        )

    def _failure(
        self,
        context: QueryContext,
        reason: DegradedReason,
        *,
        extra_reasons: tuple[str, ...] = (),
    ) -> AgentOutcome:
        message = localized(
            context.locale,
            ja="回答の生成中に問題が発生したため、この質問には回答できませんでした。"
            "時間をおいて再試行してください。",
            en="The answer could not be generated. Please try again later.",
        )
        response = CanonicalResponse(
            status=ResponseStatus.ERROR,
            blocks=(ResponseBlock(kind=ResponseBlockKind.TEXT, text=message),),
            citations=(),
            confidence=0.0,
            safe_error=message,
        )
        return AgentOutcome(
            response=response,
            diagnostics=AgentDiagnostics(
                degraded_reasons=tuple(dict.fromkeys((*extra_reasons, reason.value))),
            ),
        )
