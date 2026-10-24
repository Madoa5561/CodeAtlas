"""Validated, authorized, bounded tool execution boundary."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from codeatlas.application.tools.registry import RegisteredTool, ToolRegistry
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import ToolResult
from codeatlas.domain.agent_models import (
    CanonicalToolCall,
    EvidenceCandidate,
    ToolContext,
    ToolDefinition,
    ToolErrorCategory,
    ToolOutcome,
)
from codeatlas.domain.errors import (
    CodeAtlasError,
    ForbiddenError,
    IndexingError,
    IndexQueryError,
    InputValidationError,
    LimitExceededError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    RepositoryError,
    RequestTimeoutError,
    SecurityError,
)

_INVALID_ARGUMENTS = "The tool arguments are invalid."
_TOOL_UNAVAILABLE = "The requested tool is unavailable."
_TIMED_OUT = "The tool call timed out."
_RATE_LIMITED = "The tool call is temporarily rate limited."
_PROVIDER_FAILED = "The retrieval provider is temporarily unavailable."
_INDEX_FAILED = "The code index is unavailable."
_LIMIT_EXCEEDED = "The tool call exceeded a configured safety limit."
_FORBIDDEN = "The requested operation is not permitted."
_INTERNAL_FAILED = "The tool call failed safely."


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    """Hard bounds applied by the executor to every tool call."""

    timeout_seconds: float = 10.0
    max_payload_chars: int = 60_000
    max_attempts: int = 1
    max_evidence: int = 50

    def __post_init__(self) -> None:
        if not 0.05 <= self.timeout_seconds <= 120.0:
            raise ValueError("invalid tool timeout")
        if not 1_000 <= self.max_payload_chars <= 99_000:
            raise ValueError("invalid tool payload cap")
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("invalid tool attempt count")
        if not 1 <= self.max_evidence <= 1_000:
            raise ValueError("invalid tool evidence cap")


class ToolExecutor:
    """Implement ``ToolExecutorPort`` with strict validation and safe failures."""

    def __init__(
        self,
        registry: ToolRegistry,
        services: ToolServices,
        policy: ToolExecutionPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._services = services
        self._policy = policy or ToolExecutionPolicy()

    def catalog(self) -> tuple[ToolDefinition, ...]:
        """Return the allowlisted tool definitions safe to expose to a model."""
        return self._registry.definitions()

    async def execute(self, call: CanonicalToolCall, context: ToolContext) -> ToolOutcome:
        """Validate, authorize by tool name, and execute one bounded tool call."""
        tool = self._registry.get(call.tool_name)
        if tool is None:
            return self._error(call.tool_name, ToolErrorCategory.NOT_FOUND, _TOOL_UNAVAILABLE)
        try:
            arguments = tool.input_model.model_validate(call.arguments(), strict=True)
        except PydanticValidationError:
            return self._error(call.tool_name, ToolErrorCategory.INVALID_INPUT, _INVALID_ARGUMENTS)
        attempt = 0
        while True:
            attempt += 1
            outcome = await self._attempt(tool, call, context, arguments)
            if outcome.ok or not outcome.retryable or attempt >= self._policy.max_attempts:
                return outcome

    async def _attempt(
        self,
        tool: RegisteredTool,
        call: CanonicalToolCall,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolOutcome:
        try:
            async with asyncio.timeout(self._policy.timeout_seconds):
                result = await tool.handler(self._services, context, arguments)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._error(
                call.tool_name, ToolErrorCategory.TIMEOUT, _TIMED_OUT, retryable=True
            )
        except CodeAtlasError as error:
            return self._map_error(call.tool_name, error)
        except Exception:
            return self._error(call.tool_name, ToolErrorCategory.INTERNAL_ERROR, _INTERNAL_FAILED)
        try:
            return self._success(call.tool_name, result)
        except Exception:
            return self._error(call.tool_name, ToolErrorCategory.INTERNAL_ERROR, _INTERNAL_FAILED)

    def _success(self, tool_name: str, result: ToolResult) -> ToolOutcome:
        evidence, evidence_truncated = self._bound_evidence(result.evidence)
        payload_json, payload_truncated = self._serialize_payload(result.payload, result.truncated)
        return ToolOutcome(
            tool_name=tool_name,
            ok=True,
            payload_json=payload_json,
            evidence=evidence,
            truncated=result.truncated or payload_truncated or evidence_truncated,
        )

    def _bound_evidence(
        self, evidence: tuple[EvidenceCandidate, ...]
    ) -> tuple[tuple[EvidenceCandidate, ...], bool]:
        unique: list[EvidenceCandidate] = []
        seen: set[str] = set()
        for candidate in evidence:
            if candidate.evidence_id in seen:
                continue
            seen.add(candidate.evidence_id)
            unique.append(candidate)
        maximum = min(self._policy.max_evidence, self._services.limits.max_results)
        dropped = len(unique) > maximum
        return tuple(unique[:maximum]), dropped

    def _serialize_payload(self, payload: dict[str, Any], truncated: bool) -> tuple[str, bool]:
        document: dict[str, Any] = dict(payload)
        document["truncated"] = bool(truncated)
        encoded = _dump(document)
        if len(encoded) <= self._policy.max_payload_chars:
            return encoded, bool(truncated)
        shrunk = _shrink_document(document, self._policy.max_payload_chars)
        return _dump(shrunk), True

    @staticmethod
    def _error(
        tool_name: str,
        category: ToolErrorCategory,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> ToolOutcome:
        return ToolOutcome(
            tool_name=tool_name,
            ok=False,
            error_category=category,
            safe_message=safe_message,
            retryable=retryable,
        )

    def _map_error(self, tool_name: str, error: CodeAtlasError) -> ToolOutcome:
        message = error.safe_message or _INTERNAL_FAILED
        if isinstance(error, ForbiddenError | SecurityError):
            return self._error(tool_name, ToolErrorCategory.FORBIDDEN, message or _FORBIDDEN)
        if isinstance(error, NotFoundError):
            return self._error(tool_name, ToolErrorCategory.NOT_FOUND, message)
        if isinstance(error, InputValidationError):
            return self._error(tool_name, ToolErrorCategory.INVALID_INPUT, message)
        if isinstance(error, RequestTimeoutError):
            return self._error(tool_name, ToolErrorCategory.TIMEOUT, message, retryable=True)
        if isinstance(error, RateLimitedError):
            return self._error(tool_name, ToolErrorCategory.RATE_LIMITED, message, retryable=True)
        if isinstance(error, LimitExceededError):
            return self._error(tool_name, ToolErrorCategory.LIMIT_EXCEEDED, message)
        if isinstance(error, ProviderError):
            return self._error(tool_name, ToolErrorCategory.PROVIDER_ERROR, message, retryable=True)
        if isinstance(error, IndexQueryError | RepositoryError | IndexingError):
            return self._error(tool_name, ToolErrorCategory.PROVIDER_ERROR, message)
        return self._error(tool_name, ToolErrorCategory.INTERNAL_ERROR, _INTERNAL_FAILED)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _encoded_size(value: Any) -> int:
    return len(_dump(value))


def _iter_lists(value: Any) -> Iterator[list[Any]]:
    if isinstance(value, list):
        yield value
        for item in value:
            yield from _iter_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_lists(item)


def _iter_string_slots(value: Any) -> Iterator[tuple[Any, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                yield value, key
            else:
                yield from _iter_string_slots(item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                yield value, index
            else:
                yield from _iter_string_slots(item)


def _shrink_document(document: dict[str, Any], cap: int) -> dict[str, Any]:
    """Deterministically shrink a JSON document until it fits the payload cap."""
    data: dict[str, Any] = copy.deepcopy(document)
    data["truncated"] = True
    while _encoded_size(data) > cap:
        lists = [item for item in _iter_lists(data) if item]
        if not lists:
            break
        max(lists, key=_encoded_size).pop()
    while _encoded_size(data) > cap:
        slots = list(_iter_string_slots(data))
        if not slots:
            break
        container, key = max(slots, key=lambda slot: len(str(slot[0][slot[1]])))
        current = str(container[key])
        if len(current) <= 16:
            break
        replacement = current[: max(8, len(current) // 2)]
        if isinstance(container, list):
            container[int(key)] = replacement
        else:
            container[str(key)] = replacement
    if _encoded_size(data) > cap:
        return {"truncated": True}
    return data
