"""Symbol, reference, and relation graph retrieval tools over the active index."""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import PurePosixPath

from codeatlas.application.tools.evidence import (
    make_evidence,
    relation_evidence,
    symbol_evidence,
)
from codeatlas.application.tools.resolver import (
    ToolServices,
    build_symbol_query,
    clamp_limit,
    parse_path,
    relation_payload,
    require_snapshot,
    resolve_repository,
    symbol_payload,
)
from codeatlas.application.tools.schemas import (
    FindCalleesInput,
    FindCallersInput,
    FindDefinitionInput,
    FindExportsInput,
    FindReferencesInput,
    FindSymbolInput,
    GetDependenciesInput,
    GetDependentsInput,
    SearchLexicalInput,
    ToolResult,
    TraverseGraphInput,
)
from codeatlas.domain.agent_models import EvidenceCandidate, EvidenceKind, ToolContext
from codeatlas.domain.errors import NotFoundError
from codeatlas.domain.indexing_models import IndexedFileId, IndexedSymbol, SymbolId
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryPath
from codeatlas.domain.retrieval_models import IndexedRelation, IndexTextHit, RelationKind

_REFERENCE_KINDS = (
    RelationKind.CALL,
    RelationKind.EXTENDS,
    RelationKind.IMPLEMENTS,
    RelationKind.REFERENCE,
)

_EXPORT_MANIFEST_NAMES = frozenset({"package.json", "deno.json", "deno.jsonc"})
_MANIFEST_SEARCH_PATTERN = '"exports"'
_MAX_MANIFEST_BYTES = 512_000
_MAX_EXPORT_TARGETS = 20
_MAX_PUBLIC_MATCHES = 5


def _manifest_export_targets(content: str) -> tuple[str, ...]:
    """Return string export targets from a package/deno manifest."""
    try:
        payload = json.loads(content)
    except ValueError:
        return ()
    if not isinstance(payload, dict):
        return ()
    exports = payload.get("exports")
    if not isinstance(exports, dict):
        return ()
    targets: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            if value.startswith("./"):
                targets.append(value)
            return
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(exports)
    return tuple(dict.fromkeys(targets))[:_MAX_EXPORT_TARGETS]


def _public_export_match(content: str, name: str) -> tuple[int, int, str] | None:
    """Find a direct or block export of ``name`` in one entrypoint file."""
    escaped = re.escape(name)
    patterns = (
        rf"export\s+(?:type\s+)?\{{[^}}]*\b{escaped}\b[^}}]*\}}\s*from",
        rf"export\s+(?:default\s+)?(?:abstract\s+)?"
        rf"(?:class|interface|function|const|let|var|enum|type)\s+{escaped}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.DOTALL)
        if match is None:
            continue
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        return start_line, end_line, " ".join(match.group(0).split())
    return None


def _line_for_fragment(content: str, fragment: str) -> int:
    index = content.find(fragment)
    if index < 0:
        return 1
    return content.count("\n", 0, index) + 1


async def _public_export_evidence(
    services: ToolServices,
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    generation_id: str,
    name: str,
) -> tuple[tuple[dict[str, object], ...], tuple[EvidenceCandidate, ...]]:
    """Resolve package manifests and entrypoints to public re-export evidence."""
    if not name:
        return (), ()
    hits = await _run_sync_search(
        services,
        generation_id,
        _MANIFEST_SEARCH_PATTERN,
    )
    manifests = tuple(
        dict.fromkeys(
            hit.path
            for hit in hits
            if hit.path.as_posix().rsplit("/", 1)[-1] in _EXPORT_MANIFEST_NAMES
            and "node_modules/" not in f"{hit.path.as_posix()}/"
        )
    )[:_MAX_EXPORT_TARGETS]
    public: list[dict[str, object]] = []
    evidence: list[EvidenceCandidate] = []
    for manifest_path in manifests:
        manifest = await services.index.get_file(generation_id, manifest_path)
        if (
            manifest is None
            or manifest.content is None
            or len(manifest.content) > _MAX_MANIFEST_BYTES
        ):
            continue
        targets = _manifest_export_targets(manifest.content)
        if not targets:
            continue
        parent = posixpath.dirname(manifest_path.as_posix())
        for raw_target in targets:
            target = posixpath.normpath(posixpath.join(parent, raw_target.removeprefix("./")))
            if target.startswith("../") or target.startswith("/"):
                continue
            target_path = RepositoryPath(PurePosixPath(target)) if target else RepositoryPath.root()
            entrypoint = await services.index.get_file(generation_id, target_path)
            if entrypoint is None or entrypoint.content is None:
                continue
            match = _public_export_match(entrypoint.content, name)
            if match is None:
                continue
            start_line, end_line, matched = match
            manifest_line = _line_for_fragment(manifest.content, raw_target)
            public.append(
                {
                    "manifest": manifest_path.as_posix(),
                    "manifest_line": manifest_line,
                    "entrypoint": target,
                    "matched": matched[:300],
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
            evidence.append(
                make_evidence(
                    kind=EvidenceKind.FILE_EXCERPT,
                    descriptor=descriptor,
                    commit_sha=commit_sha,
                    path=manifest_path,
                    start_line=manifest_line,
                    end_line=manifest_line,
                    excerpt=f"package exports: {raw_target}",
                    retrieval_method="index_export_manifest",
                )
            )
            evidence.append(
                make_evidence(
                    kind=EvidenceKind.FILE_EXCERPT,
                    descriptor=descriptor,
                    commit_sha=commit_sha,
                    path=target_path,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    excerpt=f"public export: {matched}",
                    retrieval_method="index_export_public",
                )
            )
            if len(public) >= _MAX_PUBLIC_MATCHES:
                return tuple(public), tuple(evidence)
    return tuple(public), tuple(evidence)


async def _run_sync_search(
    services: ToolServices,
    generation_id: str,
    pattern: str,
) -> tuple[IndexTextHit, ...]:
    """Search indexed text with a small helper that keeps the tool code readable."""
    return await services.index.search_text(
        generation_id,
        pattern=pattern,
        case_sensitive=True,
        limit=_MAX_EXPORT_TARGETS,
    )


async def _symbol_paths(
    services: ToolServices, generation_id: str, symbols: tuple[IndexedSymbol, ...]
) -> dict[IndexedFileId, RepositoryPath]:
    file_ids = tuple({symbol.file_id for symbol in symbols})
    if not file_ids:
        return {}
    return await services.index.list_file_paths(generation_id, file_ids=file_ids)


def _symbol_evidence(
    services: ToolServices,
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    symbols: tuple[IndexedSymbol, ...],
    paths: dict[IndexedFileId, RepositoryPath],
    *,
    retrieval_method: str,
) -> tuple[EvidenceCandidate, ...]:
    return tuple(
        symbol_evidence(
            descriptor,
            commit_sha,
            symbol,
            paths[symbol.file_id],
            retrieval_method=retrieval_method,
        )
        for symbol in symbols
        if symbol.file_id in paths
    )


async def _lookup_symbols(
    services: ToolServices,
    generation_id: str,
    needle: str,
    *,
    limit: int,
) -> tuple[IndexedSymbol, ...]:
    """Look symbols up by exact name, then by qualified-name suffix."""
    by_name = await services.index.find_symbols(
        generation_id,
        build_symbol_query(
            name=needle,
            qualified_name=None,
            kinds=[],
            path_prefix=None,
            limit=limit,
        ),
    )
    if by_name:
        return by_name
    return await services.index.find_symbols(
        generation_id,
        build_symbol_query(
            name=None,
            qualified_name=needle,
            kinds=[],
            path_prefix=None,
            limit=limit,
        ),
    )


async def search_lexical(
    services: ToolServices, context: ToolContext, arguments: SearchLexicalInput
) -> ToolResult:
    """Return BM25-ranked lexical matches from the FTS5 projection."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    path = (
        parse_path(arguments.path_prefix, allow_root=True)
        if arguments.path_prefix is not None
        else None
    )
    prefix = path.as_posix() if path is not None and not path.is_root else None
    hits = await services.index.search_lexical(
        generation_id,
        query=arguments.query,
        limit=limit,
        path_prefix=prefix,
    )
    best = max((hit.score for hit in hits), default=0.0)
    evidence = tuple(
        make_evidence(
            kind=EvidenceKind.FILE_EXCERPT,
            descriptor=descriptor,
            commit_sha=snapshot.commit_sha,
            path=hit.path,
            start_line=hit.line,
            end_line=hit.line,
            excerpt=hit.excerpt,
            retrieval_method="index_fts",
            confidence=0.8 if best <= 0.0 else min(1.0, 0.5 + (0.5 * hit.score / best)),
        )
        for hit in hits
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "query": arguments.query,
            "hits": [
                {
                    "path": hit.path.as_posix(),
                    "line": hit.line,
                    "excerpt": hit.excerpt,
                    "score": round(hit.score, 6),
                }
                for hit in hits
            ],
            "count": len(hits),
        },
        evidence=evidence,
        truncated=len(hits) >= limit,
    )


async def find_symbol(
    services: ToolServices, context: ToolContext, arguments: FindSymbolInput
) -> ToolResult:
    """Find declarations by exact name and/or qualified-name suffix."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    query = build_symbol_query(
        name=arguments.name,
        qualified_name=arguments.qualified_name,
        kinds=arguments.kinds,
        path_prefix=arguments.path_prefix,
        limit=limit,
    )
    symbols = await services.index.find_symbols(generation_id, query)
    paths = await _symbol_paths(services, generation_id, symbols)
    method = "index_symbol_exact" if arguments.name is not None else "index_symbol_qualified"
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "symbols": [
                symbol_payload(symbol, paths[symbol.file_id])
                for symbol in symbols
                if symbol.file_id in paths
            ],
            "count": len(symbols),
        },
        evidence=_symbol_evidence(
            services, descriptor, snapshot.commit_sha, symbols, paths, retrieval_method=method
        ),
        truncated=len(symbols) >= limit,
    )


async def traverse_graph(
    services: ToolServices, context: ToolContext, arguments: TraverseGraphInput
) -> ToolResult:
    """Traverse call/dependency relations with bounded depth and breadth."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    kinds = tuple(RelationKind(name) for name in arguments.edge_kinds)
    direction = arguments.direction
    queue: list[tuple[str, str, int]] = []
    nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []
    evidence: list[EvidenceCandidate] = []

    def add_node(ref: str, kind: str, label: str, path: str | None = None) -> None:
        if ref in nodes:
            return
        nodes[ref] = {"ref": ref, "kind": kind, "label": label, "path": path}

    if arguments.start_type == "symbol":
        symbols = await _lookup_symbols(services, generation_id, arguments.start, limit=5)
        if not symbols:
            raise NotFoundError("The graph start symbol was not found.")
        for symbol in symbols:
            ref = f"symbol:{symbol.symbol_id.value}"
            add_node(ref, "symbol", symbol.qualified_name)
            queue.append(("symbol", symbol.symbol_id.value, 0))
    else:
        start_path = parse_path(arguments.start, allow_root=False)
        ref = f"file:{start_path.as_posix()}"
        add_node(ref, "file", start_path.as_posix(), start_path.as_posix())
        queue.append(("file", start_path.as_posix(), 0))

    visited: set[tuple[str, str]] = set()
    while queue and len(edges) < limit:
        kind, value, depth = queue.pop(0)
        key = (kind, value)
        if key in visited:
            continue
        visited.add(key)
        current_ref = f"symbol:{value}" if kind == "symbol" else f"file:{value}"
        relations: list[IndexedRelation] = []
        remaining = max(1, limit - len(edges))
        if kind == "symbol":
            symbol_id = SymbolId(value)
            if direction in {"outgoing", "both"}:
                relations.extend(
                    await services.index.find_relations_filtered(
                        generation_id,
                        relation_kinds=kinds,
                        source_symbol_id=symbol_id,
                        limit=remaining,
                    )
                )
            if direction in {"incoming", "both"}:
                current_symbol = await services.index.get_symbol(generation_id, symbol_id)
                if current_symbol is not None:
                    for target in dict.fromkeys(
                        (current_symbol.name, current_symbol.qualified_name)
                    ):
                        relations.extend(
                            await services.index.find_relations_filtered(
                                generation_id,
                                relation_kinds=kinds,
                                raw_target=target,
                                limit=remaining,
                            )
                        )
        elif direction in {"outgoing", "both"} and value != ".":
            relations.extend(
                await services.index.find_relations_filtered(
                    generation_id,
                    relation_kinds=kinds,
                    path=RepositoryPath(PurePosixPath(value)),
                    limit=remaining,
                )
            )

        seen_relations: set[tuple[str, int, str, str]] = set()
        for relation in relations:
            if len(edges) >= limit:
                break
            relation_key = (
                relation.path.as_posix(),
                relation.span.start_byte,
                relation.raw_target,
                relation.relation_kind.value,
            )
            if relation_key in seen_relations:
                continue
            seen_relations.add(relation_key)
            source_is_current = (
                kind == "symbol"
                and relation.source_symbol_id is not None
                and relation.source_symbol_id.value == value
            )
            if source_is_current:
                target_name = relation.raw_target
                neighbor_ref = f"symbol:{target_name}"
                matches = await _lookup_symbols(
                    services, generation_id, target_name.split(".")[-1], limit=3
                )
                if matches:
                    neighbor_ref = f"symbol:{matches[0].symbol_id.value}"
                    add_node(neighbor_ref, "symbol", matches[0].qualified_name)
                    if depth + 1 < arguments.max_depth:
                        queue.append(("symbol", matches[0].symbol_id.value, depth + 1))
                else:
                    add_node(neighbor_ref, "symbol", target_name)
                source_ref = current_ref
            else:
                if relation.source_symbol_id is not None:
                    neighbor_ref = f"symbol:{relation.source_symbol_id.value}"
                    source_symbol = await services.index.get_symbol(
                        generation_id, relation.source_symbol_id
                    )
                    label = (
                        source_symbol.qualified_name
                        if source_symbol is not None
                        else relation.raw_target
                    )
                    add_node(neighbor_ref, "symbol", label)
                    if depth + 1 < arguments.max_depth:
                        queue.append(("symbol", relation.source_symbol_id.value, depth + 1))
                else:
                    neighbor_ref = f"file:{relation.path.as_posix()}"
                    add_node(
                        neighbor_ref, "file", relation.path.as_posix(), relation.path.as_posix()
                    )
                source_ref = neighbor_ref
                target_ref = current_ref
                edges.append(
                    {
                        "from": source_ref,
                        "to": target_ref,
                        "kind": relation.relation_kind.value,
                        "path": relation.path.as_posix(),
                        "line": relation.span.start_line,
                        "raw_target": relation.raw_target,
                        "depth": depth + 1,
                    }
                )
                evidence.append(
                    relation_evidence(
                        descriptor,
                        snapshot.commit_sha,
                        relation,
                        retrieval_method="index_graph",
                    )
                )
                continue
            edges.append(
                {
                    "from": source_ref,
                    "to": neighbor_ref,
                    "kind": relation.relation_kind.value,
                    "path": relation.path.as_posix(),
                    "line": relation.span.start_line,
                    "raw_target": relation.raw_target,
                    "depth": depth + 1,
                }
            )
            evidence.append(
                relation_evidence(
                    descriptor,
                    snapshot.commit_sha,
                    relation,
                    retrieval_method="index_graph",
                )
            )

    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "start": arguments.start,
            "start_type": arguments.start_type,
            "direction": direction,
            "edge_kinds": list(arguments.edge_kinds),
            "nodes": list(nodes.values()),
            "edges": edges,
            "count": len(edges),
        },
        evidence=tuple(evidence),
        truncated=len(edges) >= limit,
    )


async def find_definition(
    services: ToolServices, context: ToolContext, arguments: FindDefinitionInput
) -> ToolResult:
    """Find the declaration of an exact name, falling back to a qualified suffix."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    symbols = await services.index.find_symbols(
        generation_id,
        build_symbol_query(
            name=arguments.name,
            qualified_name=None,
            kinds=arguments.kinds,
            path_prefix=arguments.path_prefix,
            limit=limit,
        ),
    )
    fallback = False
    if not symbols:
        fallback = True
        symbols = await services.index.find_symbols(
            generation_id,
            build_symbol_query(
                name=None,
                qualified_name=arguments.name,
                kinds=arguments.kinds,
                path_prefix=arguments.path_prefix,
                limit=limit,
            ),
        )
    paths = await _symbol_paths(services, generation_id, symbols)
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "name": arguments.name,
            "fallback_qualified": fallback,
            "symbols": [
                symbol_payload(symbol, paths[symbol.file_id])
                for symbol in symbols
                if symbol.file_id in paths
            ],
            "count": len(symbols),
        },
        evidence=_symbol_evidence(
            services,
            descriptor,
            snapshot.commit_sha,
            symbols,
            paths,
            retrieval_method="index_definition",
        ),
        truncated=len(symbols) >= limit,
    )


def _reference_payload(
    relation: IndexedRelation, caller_symbol_name: str | None = None
) -> dict[str, object]:
    payload = relation_payload(relation)
    if caller_symbol_name is not None:
        payload["caller_symbol_name"] = caller_symbol_name
    return payload


async def find_exports(
    services: ToolServices, context: ToolContext, arguments: FindExportsInput
) -> ToolResult:
    """Check exported symbols and export relations for a name or file."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    path = parse_path(arguments.path, allow_root=False) if arguments.path is not None else None
    path_prefix = arguments.path

    symbols: tuple[IndexedSymbol, ...]
    if arguments.name is not None:
        symbols = await services.index.find_symbols(
            generation_id,
            build_symbol_query(
                name=arguments.name,
                qualified_name=None,
                kinds=[],
                path_prefix=path_prefix,
                limit=limit * 3,
            ),
        )
        if not symbols:
            symbols = await services.index.find_symbols(
                generation_id,
                build_symbol_query(
                    name=None,
                    qualified_name=arguments.name,
                    kinds=[],
                    path_prefix=path_prefix,
                    limit=limit * 3,
                ),
            )
    else:
        symbols = await services.index.list_symbols(generation_id, path=path, limit=limit * 5)
    exported = tuple(symbol for symbol in symbols if symbol.exported)
    relations = await services.index.find_relations_filtered(
        generation_id,
        relation_kinds=(RelationKind.EXPORT,),
        raw_target=arguments.name,
        path=path,
        limit=limit,
    )
    paths = await _symbol_paths(services, generation_id, exported)
    selected_symbols = tuple(symbol for symbol in exported if symbol.file_id in paths)
    public_exports, public_evidence = await _public_export_evidence(
        services,
        descriptor,
        snapshot.commit_sha,
        generation_id,
        arguments.name or "",
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "name": arguments.name,
            "path": path.as_posix() if path is not None else None,
            "is_exported": bool(selected_symbols or relations),
            "is_publicly_exported": bool(public_exports),
            "exported_symbols": [
                symbol_payload(symbol, paths[symbol.file_id]) for symbol in selected_symbols
            ],
            "export_relations": [relation_payload(relation) for relation in relations],
            "public_exports": list(public_exports),
            "count": len(selected_symbols) + len(relations) + len(public_exports),
        },
        evidence=(
            *(
                symbol_evidence(
                    descriptor,
                    snapshot.commit_sha,
                    symbol,
                    paths[symbol.file_id],
                    retrieval_method="index_export_symbol",
                    excerpt_prefix="exported declaration: ",
                )
                for symbol in selected_symbols
            ),
            *(
                relation_evidence(
                    descriptor,
                    snapshot.commit_sha,
                    relation,
                    retrieval_method="index_export_relation",
                    excerpt_prefix="export relation: ",
                )
                for relation in relations
            ),
            *public_evidence,
        ),
        truncated=len(exported) >= limit * 3 or len(relations) >= limit,
    )


async def find_references(
    services: ToolServices, context: ToolContext, arguments: FindReferencesInput
) -> ToolResult:
    """Find incoming reference relations whose target matches the requested name."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    targets = [arguments.name]
    if arguments.qualified_name is not None and arguments.qualified_name != arguments.name:
        targets.append(arguments.qualified_name)
    found: list[IndexedRelation] = []
    seen: set[tuple[str, int, str]] = set()
    for target in targets:
        relations = await services.index.find_relations_filtered(
            generation_id,
            relation_kinds=_REFERENCE_KINDS,
            raw_target=target,
            limit=limit,
        )
        for relation in relations:
            key = (relation.path.as_posix(), relation.span.start_byte, relation.raw_target)
            if key not in seen:
                seen.add(key)
                found.append(relation)
    found.sort(key=lambda item: (item.path.as_posix(), item.span.start_byte))
    selected = found[:limit]
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "target": arguments.qualified_name or arguments.name,
            "references": [_reference_payload(relation) for relation in selected],
            "count": len(selected),
        },
        evidence=tuple(
            relation_evidence(
                descriptor,
                snapshot.commit_sha,
                relation,
                retrieval_method="index_reference",
            )
            for relation in selected
        ),
        truncated=len(found) > limit,
    )


async def find_callers(
    services: ToolServices, context: ToolContext, arguments: FindCallersInput
) -> ToolResult:
    """Find call sites whose raw target matches a callee name or dotted suffix."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    relations = await services.index.find_relations_filtered(
        generation_id,
        relation_kinds=(RelationKind.CALL,),
        raw_target=arguments.name,
        limit=limit,
    )
    caller_names: dict[str, str] = {}
    for relation in relations:
        if relation.source_symbol_id is None:
            continue
        key = relation.source_symbol_id.value
        if key in caller_names:
            continue
        symbol = await services.index.get_symbol(generation_id, relation.source_symbol_id)
        if symbol is not None:
            caller_names[key] = symbol.qualified_name
    payload_callers: list[dict[str, object]] = []
    for relation in relations:
        caller = (
            caller_names.get(relation.source_symbol_id.value)
            if relation.source_symbol_id is not None
            else None
        )
        payload_callers.append(_reference_payload(relation, caller))
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "callee": arguments.name,
            "callers": payload_callers,
            "count": len(payload_callers),
        },
        evidence=tuple(
            relation_evidence(
                descriptor,
                snapshot.commit_sha,
                relation,
                retrieval_method="index_call",
            )
            for relation in relations
        ),
        truncated=len(relations) >= limit,
    )


async def find_callees(
    services: ToolServices, context: ToolContext, arguments: FindCalleesInput
) -> ToolResult:
    """Find outgoing call relations of one declaration."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    symbols = await _lookup_symbols(services, generation_id, arguments.symbol, limit=5)
    if not symbols:
        raise NotFoundError("The requested symbol was not found in the active index.")
    paths = await _symbol_paths(services, generation_id, symbols)
    relations: list[IndexedRelation] = []
    seen: set[tuple[str, int, str]] = set()
    for symbol in symbols:
        found = await services.index.find_relations_filtered(
            generation_id,
            relation_kinds=(RelationKind.CALL,),
            source_symbol_id=symbol.symbol_id,
            limit=limit,
        )
        for relation in found:
            key = (relation.path.as_posix(), relation.span.start_byte, relation.raw_target)
            if key not in seen:
                seen.add(key)
                relations.append(relation)
    relations.sort(key=lambda item: (item.path.as_posix(), item.span.start_byte))
    selected = relations[:limit]
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "symbols": [
                symbol_payload(symbol, paths[symbol.file_id])
                for symbol in symbols
                if symbol.file_id in paths
            ],
            "callees": [_reference_payload(relation) for relation in selected],
            "count": len(selected),
        },
        evidence=tuple(
            relation_evidence(
                descriptor,
                snapshot.commit_sha,
                relation,
                retrieval_method="index_call",
            )
            for relation in selected
        ),
        truncated=len(relations) > limit,
    )


async def get_dependencies(
    services: ToolServices, context: ToolContext, arguments: GetDependenciesInput
) -> ToolResult:
    """Return import dependencies of a path, or outgoing relations of a symbol."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 200)
    if arguments.path is not None:
        target_path = parse_path(arguments.path, allow_root=True)
        imported = await services.index.find_relations_filtered(
            generation_id,
            relation_kinds=(RelationKind.IMPORT,),
            path=target_path,
            limit=limit,
        )
        return ToolResult(
            {
                "repository_id": descriptor.repository_id.value,
                "commit_sha": snapshot.commit_sha,
                "path": target_path.as_posix(),
                "dependencies": [relation_payload(relation) for relation in imported],
                "count": len(imported),
            },
            evidence=tuple(
                relation_evidence(
                    descriptor,
                    snapshot.commit_sha,
                    relation,
                    retrieval_method="index_import",
                )
                for relation in imported
            ),
            truncated=len(imported) >= limit,
        )
    assert arguments.symbol is not None
    symbols = await _lookup_symbols(services, generation_id, arguments.symbol, limit=5)
    if not symbols:
        raise NotFoundError("The requested symbol was not found in the active index.")
    paths = await _symbol_paths(services, generation_id, symbols)
    relations: list[IndexedRelation] = []
    seen: set[tuple[str, int, str]] = set()
    for symbol in symbols:
        found = await services.index.find_relations_filtered(
            generation_id,
            source_symbol_id=symbol.symbol_id,
            limit=limit,
        )
        for relation in found:
            key = (relation.path.as_posix(), relation.span.start_byte, relation.raw_target)
            if key not in seen:
                seen.add(key)
                relations.append(relation)
    relations.sort(key=lambda item: (item.path.as_posix(), item.span.start_byte))
    selected = relations[:limit]
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "symbols": [
                symbol_payload(symbol, paths[symbol.file_id])
                for symbol in symbols
                if symbol.file_id in paths
            ],
            "dependencies": [relation_payload(relation) for relation in selected],
            "count": len(selected),
        },
        evidence=tuple(
            relation_evidence(
                descriptor,
                snapshot.commit_sha,
                relation,
                retrieval_method="index_dependency",
            )
            for relation in selected
        ),
        truncated=len(relations) > limit,
    )


async def get_dependents(
    services: ToolServices, context: ToolContext, arguments: GetDependentsInput
) -> ToolResult:
    """Find relations that target a symbol name or import specifier."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    relations = await services.index.find_relations_filtered(
        generation_id,
        raw_target=arguments.target,
        limit=limit,
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "target": arguments.target,
            "dependents": [relation_payload(relation) for relation in relations],
            "count": len(relations),
        },
        evidence=tuple(
            relation_evidence(
                descriptor,
                snapshot.commit_sha,
                relation,
                retrieval_method="index_dependent",
            )
            for relation in relations
        ),
        truncated=len(relations) >= limit,
    )
