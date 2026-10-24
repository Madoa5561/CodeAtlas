"""Safe diagnostic contracts shared by the reasoning stage and the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class DegradedReason(StrEnum):
    """Stable, non-sensitive codes describing why an answer is degraded."""

    MODEL_DISABLED = "model_disabled"
    MODEL_ERROR = "model_error"
    MODEL_INVALID_JSON = "model_invalid_json"
    MODEL_UNVERIFIED_CITATIONS = "model_unverified_citations"
    MODEL_REQUIRED_BUT_UNAVAILABLE = "model_required_but_unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNEXPECTED_ERROR = "unexpected_error"
    NO_TOOLS = "no_tools"
    EMPTY_EVIDENCE = "empty_evidence"
    ITERATION_LIMIT = "iteration_limit"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"


@dataclass(frozen=True, slots=True)
class ReasonerDiagnostics:
    """Non-sensitive diagnostics recorded by the last reasoning call."""

    model_id: str = ""
    degraded_reasons: tuple[str, ...] = ()
    model_calls: int = 0

    def __post_init__(self) -> None:
        if self.model_calls < 0:
            raise ValueError("model call count cannot be negative")


@runtime_checkable
class ReasonerDiagnosticsSource(Protocol):
    """Optional observability surface implemented by concrete reasoners."""

    @property
    def last_diagnostics(self) -> ReasonerDiagnostics:
        """Return diagnostics from the most recent reason call."""
        ...
