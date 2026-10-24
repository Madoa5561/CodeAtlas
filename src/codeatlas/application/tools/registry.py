"""Immutable tool registry holding validated handlers and prompt definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import ToolResult
from codeatlas.domain.agent_models import ToolContext, ToolDefinition

ToolHandler = Callable[[ToolServices, ToolContext, BaseModel], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One tool definition bound to its strict input model and handler."""

    definition: ToolDefinition
    input_model: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    """Deterministic, append-only mapping from tool name to registered tool."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register one tool; duplicate names are rejected."""
        name = tool.definition.name
        if name in self._tools:
            raise ValueError("duplicate tool name")
        self._tools[name] = tool

    def get(self, name: str) -> RegisteredTool | None:
        """Return a registered tool or None when the name is not allowlisted."""
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return definitions in stable name order for model prompting."""
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    def names(self) -> tuple[str, ...]:
        """Return registered tool names in stable order."""
        return tuple(sorted(self._tools))
