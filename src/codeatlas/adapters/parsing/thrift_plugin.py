"""Built-in parser plugin for the bounded Thrift parser."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.adapters.parsing.thrift_parser import ThriftParser
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin
from codeatlas.plugin_api.parser import ParserDescriptor

PARSER_CAPABILITY = "parser:thrift"


@dataclass(slots=True)
class ThriftParserPlugin:
    """Lifecycle owner for the in-process Thrift parser."""

    parser: ThriftParser = field(default_factory=ThriftParser)
    started: bool = False
    descriptor = ParserDescriptor(
        parser_id=ThriftParser.parser_id,
        language_ids=ThriftParser.language_ids,
        supported_extensions=ThriftParser.supported_extensions,
        grammar_version=ThriftParser.grammar_version,
        parser_version=ThriftParser.parser_version,
        priority=90,
    )
    manifest = PluginManifest(
        plugin_id="codeatlas.parser.thrift",
        version="1.0.0",
        api_version_min="0.1.0",
        api_version_max="0.1.0",
        capabilities=(PluginCapability("parser", "thrift", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    async def start(self) -> None:
        """Enable the in-process parser without external I/O."""
        self.started = True

    async def stop(self) -> None:
        """Disable the in-process parser."""
        self.started = False
