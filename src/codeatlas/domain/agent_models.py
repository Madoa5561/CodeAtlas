"""Framework-independent contracts for the agent pipeline and tool execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from codeatlas.domain.conversation_models import ConversationId, ConversationMessage
from codeatlas.domain.indexing_models import SymbolId
from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    InteractionCapabilities,
    ResponseStatus,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath
from codeatlas.domain.retrieval_models import stable_evidence_key

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[\w:.\-]{1,200}$")
_MAX_JSON_CHARS = 100_000


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _require_json_object(value: str) -> str:
    if len(value) > _MAX_JSON_CHARS:
        raise ValueError("tool arguments are too large")
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("tool arguments must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return value


class ModelRole(StrEnum):
    """Canonical conversation roles understood by every model adapter."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelResponseFormat(StrEnum):
    """Requested response encoding for provider adapters."""

    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CanonicalToolCall:
    """Provider-neutral tool call normalized by a model adapter."""

    call_id: str
    tool_name: str
    arguments_json: str

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.call_id) is None:
            raise ValueError("invalid tool call ID")
        if _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ValueError("invalid tool name")
        _require_json_object(self.arguments_json)

    def arguments(self) -> dict[str, Any]:
        """Return the validated JSON object as a fresh dictionary."""
        parsed: Any = json.loads(self.arguments_json)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        result: dict[str, Any] = parsed
        return result


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Stable tool identity, description, and JSON Schema for model prompting."""

    name: str
    description: str
    input_schema_json: str
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if _TOOL_NAME.fullmatch(self.name) is None:
            raise ValueError("invalid tool name")
        if not self.description.strip() or len(self.description) > 1_000:
            raise ValueError("invalid tool description")
        if len(self.input_schema_json) > _MAX_JSON_CHARS:
            raise ValueError("tool schema is too large")
        try:
            schema: Any = json.loads(self.input_schema_json)
        except json.JSONDecodeError as error:
            raise ValueError("tool schema must be valid JSON") from error
        if not isinstance(schema, dict):
            raise ValueError("tool schema must be a JSON object")


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One provider-neutral message sent to a model provider."""

    role: ModelRole
    content: str
    tool_calls: tuple[CanonicalToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.content) > _MAX_JSON_CHARS:
            raise ValueError("model message is too large")
        if self.role is ModelRole.TOOL:
            if self.tool_call_id is None:
                raise ValueError("tool messages require a tool call ID")
        elif self.tool_call_id is not None:
            raise ValueError("tool call IDs are only valid for tool messages")
        if self.role is not ModelRole.ASSISTANT and self.tool_calls:
            raise ValueError("only assistant messages may carry tool calls")


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Optional provider usage counters retained for diagnostics only."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0 or self.completion_tokens < 0:
            raise ValueError("usage counters cannot be negative")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Bounded provider-neutral completion request."""

    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    temperature: float = 0.2
    max_output_tokens: int = 1_200
    timeout_seconds: float = 60.0
    response_format: ModelResponseFormat = ModelResponseFormat.TEXT

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("at least one model message is required")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("invalid model temperature")
        if not 1 <= self.max_output_tokens <= 32_000:
            raise ValueError("invalid maximum output tokens")
        if not 1.0 <= self.timeout_seconds <= 600.0:
            raise ValueError("invalid model timeout")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Provider-neutral completion result, possibly containing tool calls."""

    content: str
    tool_calls: tuple[CanonicalToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: ModelUsage = field(default_factory=ModelUsage)
    model_id: str = ""

    def __post_init__(self) -> None:
        if len(self.content) > _MAX_JSON_CHARS:
            raise ValueError("model response is too large")
        if not self.finish_reason.strip():
            raise ValueError("finish reason is required")
        if not self.content.strip() and not self.tool_calls:
            raise ValueError("model response requires content or tool calls")


class ToolErrorCategory(StrEnum):
    """Stable tool failure categories exposed to the agent and UI."""

    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    LIMIT_EXCEEDED = "limit_exceeded"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Authorization and correlation context for one tool execution."""

    request_id: str
    allowed_repository_ids: tuple[RepositoryId, ...] = ()
    default_commit_sha: str | None = None

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.request_id) is None:
            raise ValueError("invalid tool request ID")
        if self.default_commit_sha is not None and _SHA.fullmatch(self.default_commit_sha) is None:
            raise ValueError("invalid tool commit SHA")


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Bounded, safe tool result with zero or more verified evidence candidates."""

    tool_name: str
    ok: bool
    payload_json: str = "{}"
    evidence: tuple[EvidenceCandidate, ...] = ()
    error_category: ToolErrorCategory | None = None
    safe_message: str | None = None
    retryable: bool = False
    truncated: bool = False

    def __post_init__(self) -> None:
        if _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ValueError("invalid tool name")
        if len(self.payload_json) > _MAX_JSON_CHARS:
            raise ValueError("tool payload is too large")
        _require_json_object(self.payload_json)
        if self.ok:
            if self.error_category is not None or self.safe_message is not None:
                raise ValueError("successful tool outcomes cannot carry an error")
        else:
            if self.error_category is None or not (self.safe_message or "").strip():
                raise ValueError("failed tool outcomes require a category and safe message")
            if self.evidence:
                raise ValueError("failed tool outcomes cannot carry evidence")


class EvidenceKind(StrEnum):
    """Verified retrieval evidence categories with distinct provenance rules."""

    FILE_EXCERPT = "file_excerpt"
    SYMBOL = "symbol"
    IMPORT = "import"
    REFERENCE = "reference"
    COMMIT = "commit"
    TREE_ENTRY = "tree_entry"
    PULL_REQUEST = "pull_request"
    ISSUE = "issue"


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """One ranked candidate tied to an immutable commit and source span."""

    evidence_id: str
    kind: EvidenceKind
    repository_id: RepositoryId
    commit_sha: str
    path: RepositoryPath
    start_line: int
    end_line: int
    excerpt: str
    retrieval_method: str
    confidence: float = 1.0
    verified: bool = True
    symbol_id: SymbolId | None = None
    symbol_name: str | None = None
    external_url: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence_id.startswith("ev_"):
            raise ValueError("invalid evidence ID")
        if _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("evidence requires a full commit SHA")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid evidence line range")
        if not self.excerpt.strip() or len(self.excerpt) > 8_000:
            raise ValueError("invalid evidence excerpt")
        if not self.retrieval_method.strip() or len(self.retrieval_method) > 64:
            raise ValueError("invalid evidence retrieval method")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid evidence confidence")
        if self.external_url is not None and (
            len(self.external_url) > 2_048
            or not self.external_url.startswith(("https://", "http://"))
        ):
            raise ValueError("invalid evidence external URL")

    @classmethod
    def create(
        cls,
        *,
        kind: EvidenceKind,
        repository_id: RepositoryId,
        commit_sha: str,
        path: RepositoryPath,
        start_line: int,
        end_line: int,
        excerpt: str,
        retrieval_method: str,
        confidence: float = 1.0,
        verified: bool = True,
        symbol_id: SymbolId | None = None,
        symbol_name: str | None = None,
        external_url: str | None = None,
    ) -> EvidenceCandidate:
        """Build an evidence candidate with a deterministic provenance identity."""
        evidence_id = stable_evidence_key(
            kind.value,
            repository_id.value,
            commit_sha,
            path.as_posix(),
            str(start_line),
            str(end_line),
            symbol_id.value if symbol_id is not None else "",
            retrieval_method,
            excerpt,
        )
        return cls(
            evidence_id=evidence_id,
            kind=kind,
            repository_id=repository_id,
            commit_sha=commit_sha,
            path=path,
            start_line=start_line,
            end_line=end_line,
            excerpt=excerpt,
            retrieval_method=retrieval_method,
            confidence=confidence,
            verified=verified,
            symbol_id=symbol_id,
            symbol_name=symbol_name,
            external_url=external_url,
        )


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    """One evidence candidate with a ranking score and signal names."""

    evidence: EvidenceCandidate
    score: float
    rank: int
    signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.score < 0.0:
            raise ValueError("evidence score cannot be negative")
        if self.rank < 1:
            raise ValueError("evidence rank starts at one")


@dataclass(frozen=True, slots=True)
class RankedEvidenceSet:
    """Ranked evidence bounded to the configured maximum."""

    evidence: tuple[RankedEvidence, ...] = ()
    omitted_count: int = 0

    def __post_init__(self) -> None:
        if self.omitted_count < 0:
            raise ValueError("omitted evidence count cannot be negative")
        ranks = [ranked.rank for ranked in self.evidence]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("evidence ranks must be contiguous from one")


@dataclass(frozen=True, slots=True)
class InvestigationStep:
    """One planned tool call with a human-readable purpose."""

    step_id: str
    tool_name: str
    arguments_json: str
    purpose: str

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.step_id) is None:
            raise ValueError("invalid investigation step ID")
        if _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ValueError("invalid investigation tool name")
        _require_json_object(self.arguments_json)
        if not self.purpose.strip() or len(self.purpose) > 500:
            raise ValueError("invalid investigation step purpose")


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    """A bounded, ordered retrieval plan produced by the planner."""

    objective: str
    steps: tuple[InvestigationStep, ...]
    search_methods: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.objective.strip() or len(self.objective) > 1_000:
            raise ValueError("invalid investigation objective")
        if not self.steps or len(self.steps) > 20:
            raise ValueError("invalid investigation step count")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("duplicate investigation step IDs")


@dataclass(frozen=True, slots=True)
class RetrievalGap:
    """Missing information that warrants one bounded follow-up retrieval round."""

    description: str
    suggested_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.description.strip() or len(self.description) > 500:
            raise ValueError("invalid retrieval gap description")
        if any(_TOOL_NAME.fullmatch(tool) is None for tool in self.suggested_tools):
            raise ValueError("invalid suggested tool name")


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Deduplicated evidence candidates produced by one retrieval round."""

    candidates: tuple[EvidenceCandidate, ...] = ()
    search_methods: tuple[str, ...] = ()
    tool_calls_used: int = 0
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tool_calls_used < 0:
            raise ValueError("tool call count cannot be negative")
        evidence_ids = [candidate.evidence_id for candidate in self.candidates]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence candidates")


@dataclass(frozen=True, slots=True)
class ReasoningFact:
    """A statement that is directly supported by verified evidence."""

    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.text.strip() or len(self.text) > 4_000:
            raise ValueError("invalid reasoning fact")
        if not self.evidence_ids:
            raise ValueError("verified facts require evidence")


@dataclass(frozen=True, slots=True)
class ReasoningInference:
    """A model inference that must be labeled separately from verified facts."""

    text: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.text.strip() or len(self.text) > 4_000:
            raise ValueError("invalid reasoning inference")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid inference confidence")


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    """Conclusion, verified facts, inferences, and disclosed gaps."""

    conclusion: str
    facts: tuple[ReasoningFact, ...] = ()
    inferences: tuple[ReasoningInference, ...] = ()
    uncertainties: tuple[str, ...] = ()
    confidence: float = 0.0
    gaps: tuple[RetrievalGap, ...] = ()

    def __post_init__(self) -> None:
        if not self.conclusion.strip() or len(self.conclusion) > 4_000:
            raise ValueError("invalid reasoning conclusion")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid reasoning confidence")
        if any(
            not uncertainty.strip() or len(uncertainty) > 1_000
            for uncertainty in self.uncertainties
        ):
            raise ValueError("invalid uncertainty")


@dataclass(frozen=True, slots=True)
class QueryContext:
    """Application-level question context independent of any UI adapter."""

    request_id: str
    query: str
    conversation_id: ConversationId
    capabilities: InteractionCapabilities
    locale: str = "ja"
    repository_scope: tuple[RepositoryId, ...] = ()
    history: tuple[ConversationMessage, ...] = ()
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.request_id) is None:
            raise ValueError("invalid query request ID")
        if not self.query.strip() or len(self.query) > 20_000:
            raise ValueError("invalid query text")
        if not self.locale.strip() or len(self.locale) > 16:
            raise ValueError("invalid locale")
        _require_aware(self.received_at)


@dataclass(frozen=True, slots=True)
class AgentLimits:
    """Hard budgets applied to one agent investigation."""

    max_iterations: int = 3
    max_tool_calls: int = 12
    deadline_seconds: float = 90.0
    max_evidence: int = 12
    max_plan_steps: int = 6
    max_response_chars: int = 6_000
    temperature: float = 0.2
    max_output_tokens: int = 1_200
    model_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_iterations <= 10:
            raise ValueError("invalid maximum iteration count")
        if not 1 <= self.max_tool_calls <= 100:
            raise ValueError("invalid maximum tool call count")
        if not 5.0 <= self.deadline_seconds <= 600.0:
            raise ValueError("invalid agent deadline")
        if not 1 <= self.max_evidence <= 50:
            raise ValueError("invalid maximum evidence count")
        if not 1 <= self.max_plan_steps <= 20:
            raise ValueError("invalid maximum plan steps")
        if not 500 <= self.max_response_chars <= 20_000:
            raise ValueError("invalid maximum response length")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("invalid agent temperature")
        if not 1 <= self.max_output_tokens <= 32_000:
            raise ValueError("invalid agent output token limit")
        if not 1.0 <= self.model_timeout_seconds <= 600.0:
            raise ValueError("invalid agent model timeout")


@dataclass(frozen=True, slots=True)
class AgentDiagnostics:
    """Safe operational counters that never include prompts or source text."""

    iterations: int = 0
    tool_calls: int = 0
    evidence_ranked: int = 0
    search_methods: tuple[str, ...] = ()
    model_id: str = ""
    degraded_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if min(self.iterations, self.tool_calls, self.evidence_ranked) < 0:
            raise ValueError("agent counters cannot be negative")


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """Canonical response plus non-sensitive agent diagnostics."""

    response: CanonicalResponse
    diagnostics: AgentDiagnostics = field(default_factory=AgentDiagnostics)

    def __post_init__(self) -> None:
        if self.response.status is ResponseStatus.ERROR and not self.response.safe_error:
            raise ValueError("error outcomes require a safe error message")
