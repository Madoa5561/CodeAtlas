"""Dedicated bounded parser for the supported Thrift subset.

Thrift has no packaged Tree-sitter grammar in this project, so the adapter uses a
small recursive-descent parser. It extracts declarations required by M7 and emits
relation candidates for includes, extends, field types, and generated-client
heuristics. It never executes source and never exposes raw parser errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from codeatlas.domain.errors import IndexingError, LimitExceededError
from codeatlas.domain.indexing_models import (
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
from codeatlas.domain.repository_models import RepositoryPath

PARSER_ID = "thrift-recursive-descent"
PARSER_VERSION = "1.0.0"
GRAMMAR_VERSION = "thrift-0.20-subset-v1"

_BASE_TYPES = {
    "bool",
    "byte",
    "i8",
    "i16",
    "i32",
    "i64",
    "double",
    "string",
    "binary",
    "uuid",
    "void",
}
_CONTAINERS = {"list", "set", "map"}
_MODIFIERS = {"required", "optional"}
_DEFINITION_KEYWORDS = {
    "namespace",
    "include",
    "cpp_include",
    "hs_include",
    "typedef",
    "const",
    "enum",
    "struct",
    "union",
    "exception",
    "service",
}
_STOP_SYMBOLS = {"{": "}", "(": ")", "[": "]", "<": ">"}


class _ParseFailure(Exception):
    """Internal parser failure carrying a safe span."""

    def __init__(self, span: SourceSpan) -> None:
        super().__init__("thrift parse failure")
        self.span = span


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(slots=True)
class _Candidate:
    kind: SymbolKind
    name: str
    qualified: str
    signature: str
    span: SourceSpan
    parent_index: int | None
    exported: bool = True
    documented: bool = False


@dataclass(frozen=True, slots=True)
class _ReferenceCandidate:
    source_index: int | None
    raw_target: str
    span: SourceSpan
    relation_kind: str = "reference"
    confidence: float = 0.8
    resolver_metadata: str | None = None


@dataclass(frozen=True, slots=True)
class _ImportCandidate:
    raw_target: str
    span: SourceSpan


class ThriftParser:
    """Bounded parser for Thrift declarations and cross-language hints."""

    parser_id = PARSER_ID
    parser_version = PARSER_VERSION
    grammar_version = GRAMMAR_VERSION
    language_ids = ("thrift",)
    supported_extensions = (".thrift",)

    def __init__(
        self,
        *,
        max_source_bytes: int = 1_000_000,
        max_tokens: int = 200_000,
        max_depth: int = 64,
    ) -> None:
        self._max_source_bytes = max_source_bytes
        self._max_tokens = max_tokens
        self._max_depth = max_depth

    def supports(self, path: RepositoryPath) -> bool:
        return path.value.suffix.casefold() == ".thrift"

    def parse(self, snapshot: RepositorySnapshot, file: IndexedFile) -> ParsedFile:
        if file.content is None:
            raise IndexingError("Parser input content was unavailable.")
        if len(file.content.encode("utf-8")) > self._max_source_bytes:
            raise LimitExceededError("The source file exceeds the parser limit.")
        parser = _Parser(
            file.content,
            snapshot,
            file,
            max_tokens=self._max_tokens,
            max_depth=self._max_depth,
        )
        return parser.parse()


class _Parser:
    def __init__(
        self,
        text: str,
        snapshot: RepositorySnapshot,
        file: IndexedFile,
        *,
        max_tokens: int,
        max_depth: int,
    ) -> None:
        self._text = text
        self._snapshot = snapshot
        self._file = file
        self._max_tokens = max_tokens
        self._max_depth = max_depth
        self._byte_offsets = [0] * (len(text) + 1)
        self._lines = [1] * (len(text) + 1)
        self._columns = [0] * (len(text) + 1)
        byte_count = 0
        line = 1
        column = 0
        for index, char in enumerate(text):
            self._byte_offsets[index] = byte_count
            self._lines[index] = line
            self._columns[index] = column
            encoded = len(char.encode("utf-8"))
            byte_count += encoded
            if char == "\n":
                line += 1
                column = 0
            else:
                column += 1
        self._byte_offsets[len(text)] = byte_count
        self._lines[len(text)] = line
        self._columns[len(text)] = column
        self._tokens = self._tokenize()
        self._index = 0
        self._candidates: list[_Candidate] = []
        self._references: list[_ReferenceCandidate] = []
        self._imports: list[_ImportCandidate] = []
        self._errors: list[SourceSpan] = []
        self._qualified: list[str] = []

    def parse(self) -> ParsedFile:
        try:
            self._document()
        except _ParseFailure as failure:
            self._errors.append(failure.span)
        return self._build()

    # ---- lexer ---------------------------------------------------------
    def _tokenize(self) -> tuple[_Token, ...]:
        text = self._text
        tokens: list[_Token] = []
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char in " \t\r\n\f":
                index += 1
                continue
            if char == "#" or (char == "/" and index + 1 < length and text[index + 1] == "/"):
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if char == "/" and index + 1 < length and text[index + 1] == "*":
                end = text.find("*/", index + 2)
                index = length if end < 0 else end + 2
                continue
            if char in "\"'":
                start = index
                quote = char
                index += 1
                value: list[str] = []
                while index < length and text[index] != quote:
                    if text[index] == "\\" and index + 1 < length:
                        index += 1
                    value.append(text[index])
                    index += 1
                if index >= length:
                    raise _ParseFailure(self._span(start, length))
                index += 1
                tokens.append(_Token("string", "".join(value), start, index))
                continue
            if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
                start = index
                index += 1
                while index < length and (text[index].isalnum() or text[index] in "xX.+-"):
                    index += 1
                tokens.append(_Token("number", text[start:index], start, index))
                continue
            if char.isalpha() or char == "_":
                start = index
                index += 1
                while index < length and (text[index].isalnum() or text[index] in "_."):
                    index += 1
                tokens.append(_Token("identifier", text[start:index], start, index))
                continue
            if char in '{}()[],;:=<>"':
                tokens.append(_Token("symbol", char, index, index + 1))
                index += 1
                continue
            if char == ".":
                tokens.append(_Token("symbol", ".", index, index + 1))
                index += 1
                continue
            # Unsupported character: record and skip rather than treating it as code.
            self._errors.append(self._span(index, index + 1))
            index += 1
        if len(tokens) > self._max_tokens:
            raise LimitExceededError("The source file exceeds the parser token limit.")
        end = len(text)
        tokens.append(_Token("eof", "", end, end))
        return tuple(tokens)

    # ---- token helpers -------------------------------------------------
    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._peek()
        if token.kind != "eof":
            self._index += 1
        return token

    def _accept(self, value: str) -> _Token | None:
        token = self._peek()
        if token.value == value:
            return self._advance()
        return None

    def _expect(self, value: str) -> _Token:
        token = self._accept(value)
        if token is None:
            raise _ParseFailure(self._span(self._peek().start, self._peek().end))
        return token

    def _expect_identifier(self) -> _Token:
        token = self._peek()
        if token.kind != "identifier":
            raise _ParseFailure(self._span(token.start, token.end))
        return self._advance()

    def _span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(
            start_byte=self._byte_offsets[start],
            end_byte=self._byte_offsets[end],
            start_line=self._lines[start],
            start_column=self._columns[start],
            end_line=self._lines[end],
            end_column=self._columns[end],
        )

    def _token_span(self, start: _Token, end: _Token) -> SourceSpan:
        return self._span(start.start, end.end)

    def _skip_annotations(self) -> None:
        while self._accept("(") is not None:
            depth = 1
            while depth > 0:
                token = self._peek()
                if token.kind == "eof":
                    raise _ParseFailure(self._span(token.start, token.end))
                self._advance()
                if token.value == "(":
                    depth += 1
                elif token.value == ")":
                    depth -= 1

    def _skip_value(self) -> None:
        depth = 0
        while True:
            token = self._peek()
            if token.kind == "eof":
                return
            if depth == 0 and token.value in {",", ";", "}"}:
                return
            if depth == 0 and token.kind == "identifier" and token.value in _DEFINITION_KEYWORDS:
                return
            self._advance()
            if token.value in _STOP_SYMBOLS:
                depth += 1
            elif token.value in {")", "]", ">"}:
                depth = max(0, depth - 1)

    # ---- declarations --------------------------------------------------
    def _document(self) -> None:
        while self._peek().kind != "eof":
            token = self._peek()
            if token.kind != "identifier":
                self._errors.append(self._span(token.start, token.end))
                self._advance()
                continue
            keyword = token.value
            if keyword == "namespace":
                self._parse_namespace()
            elif keyword in {"include", "cpp_include", "hs_include"}:
                self._parse_include()
            elif keyword == "typedef":
                self._parse_typedef()
            elif keyword == "const":
                self._parse_const()
            elif keyword == "enum":
                self._parse_enum()
            elif keyword in {"struct", "union", "exception"}:
                self._parse_aggregate(keyword)
            elif keyword == "service":
                self._parse_service()
            else:
                self._errors.append(self._span(token.start, token.end))
                self._advance()
                self._skip_value()
                self._accept(";")

    def _add_candidate(
        self,
        *,
        kind: SymbolKind,
        name: str,
        signature: str,
        span: SourceSpan,
        parent_index: int | None = None,
        exported: bool = True,
        documented: bool = False,
    ) -> int:
        parent_qualified = self._qualified[parent_index] if parent_index is not None else ""
        qualified = f"{parent_qualified}.{name}" if parent_qualified else name
        self._candidates.append(
            _Candidate(
                kind=kind,
                name=name,
                qualified=qualified,
                signature=signature,
                span=span,
                parent_index=parent_index,
                exported=exported,
                documented=documented,
            )
        )
        self._qualified.append(qualified)
        return len(self._candidates) - 1

    def _parse_namespace(self) -> None:
        start = self._advance()
        scope = self._expect_identifier()
        value_token = self._peek()
        if value_token.kind not in {"identifier", "string"}:
            raise _ParseFailure(self._span(value_token.start, value_token.end))
        value = self._advance()
        self._add_candidate(
            kind=SymbolKind.NAMESPACE,
            name=f"{scope.value}.{value.value}",
            signature=f"namespace {scope.value} {value.value}",
            span=self._token_span(start, value),
        )
        self._skip_annotations()
        self._accept(";")

    def _parse_include(self) -> None:
        start = self._advance()
        token = self._peek()
        if token.kind != "string":
            raise _ParseFailure(self._span(token.start, token.end))
        self._advance()
        self._imports.append(
            _ImportCandidate(raw_target=token.value[:1_024], span=self._token_span(start, token))
        )
        self._accept(";")

    def _parse_typedef(self) -> None:
        start = self._advance()
        type_name, refs = self._type()
        name = self._expect_identifier()
        span = self._token_span(start, name)
        index = self._add_candidate(
            kind=SymbolKind.TYPEDEF,
            name=name.value,
            signature=f"typedef {type_name} {name.value}",
            span=span,
        )
        for ref in refs:
            self._references.append(_ReferenceCandidate(index, ref, span))
        self._skip_annotations()
        self._accept(";")

    def _parse_const(self) -> None:
        start = self._advance()
        type_name, refs = self._type()
        name = self._expect_identifier()
        span = self._token_span(start, name)
        index = self._add_candidate(
            kind=SymbolKind.CONST,
            name=name.value,
            signature=f"const {type_name} {name.value}",
            span=span,
        )
        for ref in refs:
            self._references.append(_ReferenceCandidate(index, ref, span))
        self._accept("=")
        self._skip_value()
        self._accept(";")

    def _parse_enum(self) -> None:
        start = self._advance()
        name = self._expect_identifier()
        index = self._add_candidate(
            kind=SymbolKind.ENUM,
            name=name.value,
            signature=f"enum {name.value}",
            span=self._token_span(start, name),
        )
        self._expect("{")
        while self._peek().kind != "eof" and self._peek().value != "}":
            member = self._peek()
            if member.kind != "identifier":
                self._errors.append(self._span(member.start, member.end))
                self._advance()
                continue
            self._advance()
            self._add_candidate(
                kind=SymbolKind.FIELD,
                name=member.value,
                signature=f"{name.value}.{member.value}",
                span=self._span(member.start, member.end),
                parent_index=index,
            )
            if self._accept("=") is not None:
                self._skip_value()
            self._accept(",")
            self._accept(";")
        self._expect("}")
        self._skip_annotations()

    def _parse_aggregate(self, keyword: str) -> None:
        start = self._advance()
        name = self._expect_identifier()
        kind = {
            "struct": SymbolKind.STRUCT,
            "union": SymbolKind.UNION,
            "exception": SymbolKind.EXCEPTION,
        }[keyword]
        index = self._add_candidate(
            kind=kind,
            name=name.value,
            signature=f"{keyword} {name.value}",
            span=self._token_span(start, name),
        )
        self._expect("{")
        while self._peek().kind != "eof" and self._peek().value != "}":
            self._parse_field(index, terminator="}")
            self._accept(",")
            self._accept(";")
        self._expect("}")
        self._skip_annotations()

    def _parse_service(self) -> None:
        start = self._advance()
        name = self._expect_identifier()
        index = self._add_candidate(
            kind=SymbolKind.SERVICE,
            name=name.value,
            signature=f"service {name.value}",
            span=self._token_span(start, name),
        )
        # Heuristic cross-language edge to generated TS/JS client naming.
        self._references.append(
            _ReferenceCandidate(
                index,
                f"{name.value}Client",
                self._token_span(start, name),
                confidence=0.5,
                resolver_metadata="thrift-generated-client-heuristic",
            )
        )
        if self._accept("extends") is not None:
            parent = self._expect_identifier()
            self._references.append(
                _ReferenceCandidate(
                    index,
                    parent.value.split(".")[-1],
                    self._token_span(start, parent),
                    relation_kind="extends",
                    confidence=0.9,
                )
            )
        self._expect("{")
        while self._peek().kind != "eof" and self._peek().value != "}":
            self._parse_function(index)
        self._expect("}")
        self._skip_annotations()

    def _parse_function(self, service_index: int) -> None:
        start = self._peek()
        if self._peek().value == "oneway":
            self._advance()
        return_type, return_refs = self._type()
        name = self._expect_identifier()
        index = self._add_candidate(
            kind=SymbolKind.METHOD,
            name=name.value,
            signature=f"{return_type} {name.value}",
            span=self._token_span(start, name),
            parent_index=service_index,
        )
        for ref in return_refs:
            self._references.append(_ReferenceCandidate(index, ref, self._token_span(start, name)))
        self._expect("(")
        arg_types = self._parse_field_list(index, terminator=")")
        self._expect(")")
        throws_types: list[str] = []
        if self._accept("throws") is not None:
            self._expect("(")
            throws_types = self._parse_field_list(index, terminator=")")
            self._expect(")")
        self._skip_annotations()
        signature = f"{return_type} {name.value}({', '.join(arg_types)})"
        if throws_types:
            signature += f" throws ({', '.join(throws_types)})"
        candidate = self._candidates[index]
        candidate.signature = signature
        self._accept(",")
        self._accept(";")

    def _parse_field_list(self, parent_index: int, *, terminator: str) -> list[str]:
        types: list[str] = []
        while self._peek().kind != "eof" and self._peek().value != terminator:
            field_type = self._parse_field(parent_index, terminator=terminator)
            if field_type:
                types.append(field_type)
            if self._accept(",") is not None or self._accept(";") is not None:
                continue
            if self._peek().value != terminator:
                raise _ParseFailure(self._span(self._peek().start, self._peek().end))
        return types

    def _parse_field(self, parent_index: int, *, terminator: str) -> str:
        start = self._peek()
        field_id: str | None = None
        if self._peek().kind == "number" and self._tokens[self._index + 1].value == ":":
            field_id = self._advance().value
            self._advance()
        modifier: str | None = None
        if self._peek().value in _MODIFIERS:
            modifier = self._advance().value
        field_type, refs = self._type()
        name_token = self._peek()
        if name_token.kind != "identifier":
            raise _ParseFailure(self._span(name_token.start, name_token.end))
        self._advance()
        name = name_token.value
        signature_parts = [
            part
            for part in (
                f"{field_id}:" if field_id is not None else "",
                modifier or "",
                field_type,
                name,
            )
            if part
        ]
        span = self._token_span(start, name_token)
        index = self._add_candidate(
            kind=SymbolKind.FIELD,
            name=name,
            signature=" ".join(signature_parts),
            span=span,
            parent_index=parent_index,
        )
        for ref in refs:
            self._references.append(_ReferenceCandidate(index, ref, span))
        if self._accept("=") is not None:
            self._skip_value()
        self._skip_annotations()
        if self._peek().value not in {terminator, ",", ";", "}"}:
            raise _ParseFailure(self._span(self._peek().start, self._peek().end))
        return field_type

    def _type(self) -> tuple[str, tuple[str, ...]]:
        token = self._peek()
        if token.kind != "identifier":
            raise _ParseFailure(self._span(token.start, token.end))
        base = self._advance().value
        if base in _CONTAINERS and self._accept("<") is not None:
            parameters: list[str] = []
            refs: list[str] = []
            while self._peek().kind != "eof" and self._peek().value != ">":
                param, param_refs = self._type()
                parameters.append(param)
                refs.extend(param_refs)
                if self._accept(",") is None:
                    break
            self._expect(">")
            return f"{base}<{', '.join(parameters)}>", tuple(refs)
        if base in _BASE_TYPES:
            return base, ()
        raw = base.split(".")[-1]
        return base, (raw,)

    # ---- finalization --------------------------------------------------
    def _build(self) -> ParsedFile:
        language = "thrift"
        ids: list[SymbolId] = []
        for candidate in self._candidates:
            ids.append(
                SymbolId.derive(
                    self._snapshot.repository_id,
                    self._snapshot.commit_sha,
                    language,
                    self._file.path,
                    candidate.kind,
                    candidate.qualified,
                    candidate.signature,
                    candidate.span,
                )
            )
        symbols = tuple(
            IndexedSymbol(
                ids[index],
                self._snapshot.generation_id,
                self._file.file_id,
                candidate.kind,
                candidate.name,
                candidate.qualified,
                candidate.signature,
                candidate.span,
                ids[candidate.parent_index] if candidate.parent_index is not None else None,
                candidate.exported,
                candidate.documented,
                PARSER_ID,
                PARSER_VERSION,
                GRAMMAR_VERSION,
            )
            for index, candidate in enumerate(self._candidates)
        )
        imports = tuple(
            ImportRelation(
                self._snapshot.generation_id,
                self._file.file_id,
                None,
                item.raw_target,
                item.span,
            )
            for item in self._imports
        )
        references = tuple(
            ReferenceRelation(
                self._snapshot.generation_id,
                self._file.file_id,
                ids[item.source_index] if item.source_index is not None else None,
                item.raw_target[:1_024],
                item.span,
                relation_kind=item.relation_kind,
                confidence=item.confidence,
                resolver_metadata=item.resolver_metadata,
            )
            for item in self._references
            if item.raw_target.strip()
        )
        return ParsedFile(
            symbols,
            imports,
            (),
            references,
            len(self._errors),
            tuple(self._errors[:100]),
        )
