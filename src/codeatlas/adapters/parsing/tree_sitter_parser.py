"""Bounded Tree-sitter parser for TypeScript and JavaScript."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from codeatlas.domain.errors import IndexingError, LimitExceededError
from codeatlas.domain.indexing_models import (
    ExportRelation,
    ImportRelation,
    IndexedFile,
    IndexedSymbol,
    ParsedFile,
    ReferenceRelation,
    RepositorySnapshot,
    SourceSpan,
    SymbolId,
    SymbolKind,
)

_DECLARATIONS: dict[str, SymbolKind] = {
    "class_declaration": SymbolKind.CLASS,
    "interface_declaration": SymbolKind.INTERFACE,
    "function_declaration": SymbolKind.FUNCTION,
    "generator_function_declaration": SymbolKind.FUNCTION,
    "method_definition": SymbolKind.METHOD,
    "type_alias_declaration": SymbolKind.TYPE_ALIAS,
    "enum_declaration": SymbolKind.ENUM,
    "internal_module": SymbolKind.NAMESPACE,
    "ambient_declaration": SymbolKind.NAMESPACE,
}
_VARIABLES = {"variable_declarator"}
_ANONYMOUS = {"arrow_function", "function_expression", "generator_function"}


@dataclass(frozen=True, slots=True)
class _Candidate:
    node: Node
    kind: SymbolKind
    name: str
    qualified_name: str
    signature: str
    parent_index: int | None
    exported: bool
    documented: bool


class TreeSitterTypeScriptParser:
    """Extract bounded M3 declarations and unresolved relations from JS/TS ASTs."""

    parser_id = "codeatlas.parser.tree-sitter-typescript-javascript"
    parser_version = "1.0.0"
    grammar_version = "typescript-0.23.2/javascript-0.23.1"
    language_ids = ("typescript", "tsx", "javascript", "jsx")
    supported_extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def __init__(
        self,
        *,
        max_source_bytes: int = 1_000_000,
        max_nodes: int = 200_000,
        max_depth: int = 512,
    ) -> None:
        self.max_source_bytes = max_source_bytes
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def supports(self, path: object) -> bool:
        """Return whether a repository path has a supported JS/TS extension."""
        value = getattr(path, "value", path)
        suffix = PurePosixPath(str(value)).suffix.casefold()
        return suffix in self.supported_extensions

    def parse(self, snapshot: RepositorySnapshot, file: IndexedFile) -> ParsedFile:
        """Parse one validated file without treating source comments as instructions."""
        if file.content is None:
            raise IndexingError("Parser input content was unavailable.")
        source = file.content.encode("utf-8")
        if len(source) > self.max_source_bytes:
            raise LimitExceededError("The source file exceeds the parser limit.")
        language = self._language(file.path.value.suffix.casefold())
        try:
            tree = Parser(language).parse(source)
            return self._extract(snapshot, file, source, tree.root_node)
        except (IndexingError, LimitExceededError):
            raise
        except Exception as error:
            raise IndexingError("The source parser failed.", internal_cause=error) from error

    @staticmethod
    def _language(extension: str) -> Language:
        if extension in {".ts", ".tsx"}:
            grammar = (
                tree_sitter_typescript.language_tsx()
                if extension == ".tsx"
                else tree_sitter_typescript.language_typescript()
            )
        else:
            grammar = tree_sitter_javascript.language()
        return Language(grammar)

    def _extract(
        self, snapshot: RepositorySnapshot, file: IndexedFile, source: bytes, root: Node
    ) -> ParsedFile:
        candidates: list[_Candidate] = []
        relation_nodes: list[tuple[str, Node, int | None]] = []
        syntax_errors: list[SourceSpan] = []
        visited = 0

        def walk(node: Node, parent_index: int | None, names: tuple[str, ...], depth: int) -> None:
            nonlocal visited
            visited += 1
            if visited > self.max_nodes or depth > self.max_depth:
                raise LimitExceededError("The syntax tree exceeds the parser limit.")
            if node.is_error or node.is_missing:
                syntax_errors.append(self._span(source, node))
            current_parent = parent_index
            current_names = names
            candidate = self._candidate(source, node, parent_index, names)
            if candidate is not None:
                current_parent = len(candidates)
                candidates.append(candidate)
                current_names = (*names, candidate.name)
            if node.type == "import_statement":
                relation_nodes.append(("import", node, current_parent))
            elif node.type == "export_statement":
                relation_nodes.append(("export", node, current_parent))
            elif node.type == "call_expression":
                relation_nodes.append(("call", node, current_parent))
            elif node.type in {"extends_clause", "implements_clause", "class_heritage"}:
                relation_nodes.append((node.type, node, current_parent))
            for child in node.named_children:
                walk(child, current_parent, current_names, depth + 1)

        walk(root, None, (), 0)
        symbols: list[IndexedSymbol] = []
        ids: list[SymbolId] = []
        language = self._language_id(file.path.value.suffix.casefold())
        for candidate in candidates:
            span = self._span(source, candidate.node)
            ids.append(
                SymbolId.derive(
                    snapshot.repository_id,
                    snapshot.commit_sha,
                    language,
                    file.path,
                    candidate.kind,
                    candidate.qualified_name,
                    candidate.signature,
                    span,
                )
            )
        for index, candidate in enumerate(candidates):
            symbols.append(
                IndexedSymbol(
                    ids[index],
                    snapshot.generation_id,
                    file.file_id,
                    candidate.kind,
                    candidate.name,
                    candidate.qualified_name,
                    candidate.signature,
                    self._span(source, candidate.node),
                    ids[candidate.parent_index] if candidate.parent_index is not None else None,
                    candidate.exported,
                    candidate.documented,
                    self.parser_id,
                    self.parser_version,
                    self.grammar_version,
                )
            )
        imports: list[ImportRelation] = []
        exports: list[ExportRelation] = []
        references: list[ReferenceRelation] = []
        for kind, node, parent_index in relation_nodes:
            raw_target = self._relation_target(source, node, kind)
            if not raw_target:
                continue
            source_symbol_id = ids[parent_index] if parent_index is not None else None
            span = self._span(source, node)
            if kind == "import":
                imports.append(
                    ImportRelation(
                        snapshot.generation_id,
                        file.file_id,
                        source_symbol_id,
                        raw_target[:1_024],
                        span,
                    )
                )
            elif kind == "export":
                exports.append(
                    ExportRelation(
                        snapshot.generation_id,
                        file.file_id,
                        source_symbol_id,
                        raw_target[:1_024],
                        span,
                    )
                )
            else:
                references.append(
                    ReferenceRelation(
                        snapshot.generation_id,
                        file.file_id,
                        source_symbol_id,
                        raw_target[:1_024],
                        span,
                        relation_kind=kind,
                    )
                )
        return ParsedFile(
            tuple(symbols),
            tuple(imports),
            tuple(exports),
            tuple(references),
            len(syntax_errors),
            tuple(syntax_errors[:100]),
        )

    def _candidate(
        self,
        source: bytes,
        node: Node,
        parent_index: int | None,
        names: tuple[str, ...],
    ) -> _Candidate | None:
        kind = _DECLARATIONS.get(node.type)
        name_node = node.child_by_field_name("name")
        if node.type == "method_definition" and name_node is not None:
            name_text = self._text(source, name_node)
            if name_text == "constructor":
                kind = SymbolKind.CONSTRUCTOR
        if kind is None and node.type in _VARIABLES:
            name_node = node.child_by_field_name("name")
            kind = SymbolKind.VARIABLE
        if kind is None and node.type in _ANONYMOUS:
            kind = SymbolKind.ANONYMOUS_FUNCTION
            name = f"<anonymous@{node.start_byte}>"
        elif kind is not None:
            name = self._text(source, name_node) if name_node is not None else f"<{kind.value}>"
        else:
            return None
        signature = self._signature(source, node)
        exported = any(parent.type == "export_statement" for parent in self._ancestors(node))
        previous = node.prev_named_sibling
        documented = previous is not None and previous.type == "comment"
        qualified = ".".join((*names, name)) if names else name
        return _Candidate(
            node, kind, name, qualified, signature, parent_index, exported, documented
        )

    @staticmethod
    def _ancestors(node: Node) -> tuple[Node, ...]:
        result: list[Node] = []
        current = node.parent
        while current is not None:
            result.append(current)
            current = current.parent
        return tuple(result)

    @staticmethod
    def _text(source: bytes, node: Node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8")

    def _signature(self, source: bytes, node: Node) -> str:
        text = self._text(source, node)
        boundary = text.find("{")
        if boundary >= 0:
            text = text[:boundary]
        return " ".join(text.split())[:512]

    def _relation_target(self, source: bytes, node: Node, kind: str) -> str:
        if kind == "import":
            target = node.child_by_field_name("source")
        elif kind == "call":
            target = node.child_by_field_name("function")
        else:
            target = None
        return self._text(source, target) if target is not None else self._signature(source, node)

    @staticmethod
    def _span(source: bytes, node: Node) -> SourceSpan:
        def character_column(byte_offset: int, byte_column: int) -> int:
            return len(source[byte_offset - byte_column : byte_offset].decode("utf-8"))

        return SourceSpan(
            node.start_byte,
            node.end_byte,
            node.start_point.row + 1,
            character_column(node.start_byte, node.start_point.column),
            node.end_point.row + 1,
            character_column(node.end_byte, node.end_point.column),
        )

    @staticmethod
    def _language_id(extension: str) -> str:
        return {
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
            ".mjs": "javascript",
            ".cjs": "javascript",
        }[extension]


def installed_parser_versions() -> tuple[str, str, str]:
    """Return binding and grammar package versions for diagnostics and ADR verification."""
    return (
        version("tree-sitter"),
        version("tree-sitter-typescript"),
        version("tree-sitter-javascript"),
    )
