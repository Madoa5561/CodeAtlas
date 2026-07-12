"""Minimal metadata contract for parser plugins."""

from __future__ import annotations

from dataclasses import dataclass

from codeatlas.domain.indexing_ports import SourceParserPort
from codeatlas.plugin_api.types import PluginLifecycle


@dataclass(frozen=True, slots=True)
class ParserDescriptor:
    """Parser selection metadata validated independently from its implementation."""

    parser_id: str
    language_ids: tuple[str, ...]
    supported_extensions: tuple[str, ...]
    grammar_version: str
    parser_version: str
    priority: int = 100

    def __post_init__(self) -> None:
        if not self.parser_id or not self.language_ids or not self.supported_extensions:
            raise ValueError("parser descriptor is incomplete")
        if not 0 <= self.priority <= 10_000:
            raise ValueError("parser priority is out of range")


class ParserPlugin(PluginLifecycle):
    """Lifecycle plugin exposing one source parser implementation."""

    parser: SourceParserPort
    descriptor: ParserDescriptor
