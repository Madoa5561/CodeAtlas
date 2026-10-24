"""Ports for model providers and the five-stage agent pipeline."""

from __future__ import annotations

from typing import Protocol

from codeatlas.domain.agent_models import (
    AgentLimits,
    AgentOutcome,
    CanonicalToolCall,
    InvestigationPlan,
    ModelRequest,
    ModelResponse,
    QueryContext,
    RankedEvidenceSet,
    ReasoningResult,
    RetrievalGap,
    RetrievalResult,
    ToolContext,
    ToolDefinition,
    ToolOutcome,
)
from codeatlas.domain.interaction_models import CanonicalResponse, InteractionCapabilities


class ModelProviderPort(Protocol):
    """Provider-neutral completion boundary used only by agent components."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one bounded request or raise a safe provider error."""
        ...

    async def aclose(self) -> None:
        """Release provider resources without raising on a second call."""
        ...


class ToolExecutorPort(Protocol):
    """Validated, authorized, bounded execution boundary for every tool call."""

    def catalog(self) -> tuple[ToolDefinition, ...]:
        """Return tool definitions safe to expose to a model provider."""
        ...

    async def execute(self, call: CanonicalToolCall, context: ToolContext) -> ToolOutcome:
        """Validate and execute one tool call, returning a safe outcome."""
        ...


class PlannerPort(Protocol):
    """Builds a bounded investigation plan from a question and tool catalog."""

    async def plan(
        self,
        context: QueryContext,
        catalog: tuple[ToolDefinition, ...],
        limits: AgentLimits,
    ) -> InvestigationPlan:
        """Plan the first retrieval round."""
        ...

    async def replan(
        self,
        context: QueryContext,
        previous: InvestigationPlan,
        gaps: tuple[RetrievalGap, ...],
        catalog: tuple[ToolDefinition, ...],
        limits: AgentLimits,
    ) -> InvestigationPlan:
        """Plan one bounded follow-up round for disclosed retrieval gaps."""
        ...


class RetrieverPort(Protocol):
    """Executes a plan through the tool executor and normalizes evidence."""

    def catalog(self) -> tuple[ToolDefinition, ...]:
        """Return the tool catalog used for planning."""
        ...

    async def retrieve(
        self,
        plan: InvestigationPlan,
        context: QueryContext,
        tool_context: ToolContext,
        limits: AgentLimits,
    ) -> RetrievalResult:
        """Execute bounded plan steps and return deduplicated evidence."""
        ...


class EvidenceRankerPort(Protocol):
    """Scores and diversifies evidence candidates before reasoning."""

    async def rank(
        self,
        context: QueryContext,
        candidates: RetrievalResult,
        limits: AgentLimits,
    ) -> RankedEvidenceSet:
        """Return ranked evidence with a bounded size."""
        ...


class ReasonerPort(Protocol):
    """Produces verified facts, labeled inferences, and disclosed gaps."""

    async def reason(
        self,
        context: QueryContext,
        ranked: RankedEvidenceSet,
        limits: AgentLimits,
    ) -> ReasoningResult:
        """Reason over ranked evidence without executing tools."""
        ...


class ResponseComposerPort(Protocol):
    """Formats a canonical, citation-bearing response without adding facts."""

    async def compose(
        self,
        context: QueryContext,
        reasoning: ReasoningResult,
        ranked: RankedEvidenceSet,
        capabilities: InteractionCapabilities,
        limits: AgentLimits,
    ) -> CanonicalResponse:
        """Compose a channel-neutral response within presentation limits."""
        ...


class AgentRunnerPort(Protocol):
    """Application-facing entry point that runs the full agent pipeline."""

    async def run(self, context: QueryContext, limits: AgentLimits) -> AgentOutcome:
        """Execute the pipeline within its budget or return a safe failure."""
        ...
