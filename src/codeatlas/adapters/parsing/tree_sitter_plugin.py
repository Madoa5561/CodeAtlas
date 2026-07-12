"""Built-in parser plugin for TypeScript and JavaScript."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin
from codeatlas.plugin_api.parser import ParserDescriptor

PARSER_CAPABILITY = "parser:typescript-javascript"


@dataclass(slots=True)
class TypeScriptJavaScriptParserPlugin:
    """Lifecycle owner for the bounded built-in Tree-sitter parser."""

    parser: TreeSitterTypeScriptParser = field(default_factory=TreeSitterTypeScriptParser)
    started: bool = False
    descriptor = ParserDescriptor(
        parser_id=TreeSitterTypeScriptParser.parser_id,
        language_ids=TreeSitterTypeScriptParser.language_ids,
        supported_extensions=TreeSitterTypeScriptParser.supported_extensions,
        grammar_version=TreeSitterTypeScriptParser.grammar_version,
        parser_version=TreeSitterTypeScriptParser.parser_version,
        priority=100,
    )
    manifest = PluginManifest(
        plugin_id="codeatlas.parser.tree-sitter-typescript-javascript",
        version="1.0.0",
        api_version_min="0.1.0",
        api_version_max="0.1.0",
        capabilities=(PluginCapability("parser", "typescript-javascript", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    async def start(self) -> None:
        """Enable the in-process parser without external I/O."""
        self.started = True

    async def stop(self) -> None:
        """Disable the in-process parser."""
        self.started = False
