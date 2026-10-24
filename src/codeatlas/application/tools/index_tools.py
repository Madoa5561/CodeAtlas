"""Symbol, reference, and relation graph retrieval tools over the active index."""

from __future__ import annotations

from codeatlas.application.tools.evidence import relation_evidence, symbol_evidence
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
    FindReferencesInput,
    FindSymbolInput,
    GetDependenciesInput,
    GetDependentsInput,
    ToolResult,
)
from codeatlas.domain.agent_models import EvidenceCandidate, ToolContext
from codeatlas.domain.errors import NotFoundError
from codeatlas.domain.indexing_models import IndexedFileId, IndexedSymbol
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryPath
from codeatlas.domain.retrieval_models import IndexedRelation, RelationKind

_REFERENCE_KINDS = (
    RelationKind.CALL,
    RelationKind.EXTENDS,
    RelationKind.IMPLEMENTS,
    RelationKind.REFERENCE,
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
