"""Summary, impact, Mermaid, and PR review tools over the shared knowledge graph."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from codeatlas.application.tools.evidence import make_evidence, relation_evidence
from codeatlas.application.tools.github_tools import _repository_ref
from codeatlas.application.tools.index_tools import _lookup_symbols, traverse_graph
from codeatlas.application.tools.resolver import (
    ToolServices,
    clamp_limit,
    parse_path,
    require_snapshot,
    resolve_repository,
)
from codeatlas.application.tools.schemas import (
    AnalyzeImpactInput,
    GenerateMermaidInput,
    ReviewPullRequestInput,
    SummarizeInput,
    ToolResult,
    TraverseGraphInput,
)
from codeatlas.domain.agent_models import EvidenceCandidate, EvidenceKind, ToolContext
from codeatlas.domain.errors import NotFoundError
from codeatlas.domain.repository_models import RepositoryDescriptor, RepositoryPath
from codeatlas.domain.retrieval_models import IndexedRelation

_DIFF_PATH = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_MAX_MERMAID_CHARS = 7_000


def _structured_evidence(
    descriptor: RepositoryDescriptor,
    commit_sha: str,
    path: RepositoryPath,
    excerpt: str,
    method: str,
    *,
    kind: EvidenceKind = EvidenceKind.FILE_EXCERPT,
) -> EvidenceCandidate:
    return make_evidence(
        kind=kind,
        descriptor=descriptor,
        commit_sha=commit_sha,
        path=path,
        start_line=1,
        end_line=max(1, len(excerpt.splitlines())),
        excerpt=excerpt[:_MAX_MERMAID_CHARS],
        retrieval_method=method,
    )


async def summarize_repository(
    services: ToolServices, context: ToolContext, arguments: SummarizeInput
) -> ToolResult:
    """Summarize file and symbol structure as cited evidence."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    files = await services.index.list_files(generation_id)
    prefix = arguments.path_prefix.strip().rstrip("/") if arguments.path_prefix else None
    selected = [
        file
        for file in files
        if prefix is None
        or file.path.as_posix() == prefix
        or file.path.as_posix().startswith(prefix + "/")
    ]
    selected_paths = {file.path.as_posix() for file in selected}
    all_symbols = await services.index.list_symbols(generation_id, path=None, limit=500)
    path_by_id = {file.file_id: file.path for file in files}
    symbols = [
        symbol
        for symbol in all_symbols
        if path_by_id.get(symbol.file_id) is not None
        and path_by_id[symbol.file_id].as_posix() in selected_paths
    ]
    kinds = Counter(symbol.kind.value for symbol in symbols)
    languages = Counter(file.language or "unknown" for file in selected)
    top = symbols[: arguments.limit]
    lines = [
        "Repository summary evidence",
        f"files: {len(selected)}",
        f"languages: {', '.join(f'{name}={count}' for name, count in languages.most_common())}",
        f"symbol_kinds: {', '.join(f'{name}={count}' for name, count in kinds.most_common())}",
    ]
    if arguments.focus:
        lines.append(f"focus: {arguments.focus}")
    lines.append("top symbols:")
    for symbol in top:
        path = path_by_id.get(symbol.file_id)
        location = path.as_posix() if path else "?"
        lines.append(
            f"- {symbol.kind.value} {symbol.qualified_name} ({location}:{symbol.span.start_line})"
        )
    excerpt = "\n".join(lines)
    evidence = _structured_evidence(
        descriptor,
        snapshot.commit_sha,
        RepositoryPath(PurePosixPath("analysis/summary.md")),
        excerpt,
        "index_summary",
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "files": len(selected),
            "symbols": len(symbols),
            "languages": dict(languages),
            "symbol_kinds": dict(kinds),
            "top_symbols": [
                {
                    "kind": symbol.kind.value,
                    "name": symbol.qualified_name,
                    "span": [symbol.span.start_line, symbol.span.end_line],
                }
                for symbol in top
            ],
            "focus": arguments.focus,
        },
        evidence=(evidence,),
    )


async def analyze_impact(
    services: ToolServices, context: ToolContext, arguments: AnalyzeImpactInput
) -> ToolResult:
    """Walk reverse relations to list symbols and files affected by a change."""
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    limit = clamp_limit(arguments.limit, 100)
    impacted_files: dict[str, dict[str, object]] = {}
    impacted_symbols: dict[str, dict[str, object]] = {}
    evidence: list[EvidenceCandidate] = []
    seen_relations: set[tuple[str, int, str, str]] = set()
    frontier: list[tuple[str, str | None, int]] = []
    if arguments.start_type == "symbol":
        symbols = await _lookup_symbols(services, generation_id, arguments.start, limit=5)
        if not symbols:
            raise NotFoundError("The impact start symbol was not found.")
        for symbol in symbols:
            frontier.append((symbol.name, symbol.qualified_name, 0))
            impacted_symbols[symbol.symbol_id.value] = {
                "name": symbol.qualified_name,
                "kind": symbol.kind.value,
                "depth": 0,
            }
    else:
        path = parse_path(arguments.start, allow_root=False)
        frontier.append((path.as_posix(), None, 0))
        impacted_files[path.as_posix()] = {"path": path.as_posix(), "depth": 0}

    visited_symbols: set[str] = set()
    while frontier and len(impacted_files) + len(impacted_symbols) < limit:
        name, qualified, depth = frontier.pop(0)
        if depth >= arguments.max_depth:
            continue
        targets = [name] if qualified is None or qualified == name else [name, qualified]
        relations: list[IndexedRelation] = []
        for target in dict.fromkeys(targets):
            relations.extend(
                await services.index.find_relations_filtered(
                    generation_id,
                    raw_target=target,
                    limit=limit,
                )
            )
        for relation in relations:
            key = (
                relation.path.as_posix(),
                relation.span.start_byte,
                relation.raw_target,
                relation.relation_kind.value,
            )
            if key in seen_relations:
                continue
            seen_relations.add(key)
            if len(impacted_files) + len(impacted_symbols) >= limit:
                break
            impacted_files.setdefault(
                relation.path.as_posix(),
                {"path": relation.path.as_posix(), "depth": depth + 1},
            )
            evidence.append(
                relation_evidence(
                    descriptor,
                    snapshot.commit_sha,
                    relation,
                    retrieval_method="index_impact",
                )
            )
            if (
                relation.source_symbol_id is not None
                and relation.source_symbol_id.value not in visited_symbols
            ):
                source_symbol = await services.index.get_symbol(
                    generation_id, relation.source_symbol_id
                )
                if source_symbol is not None:
                    visited_symbols.add(source_symbol.symbol_id.value)
                    impacted_symbols[source_symbol.symbol_id.value] = {
                        "name": source_symbol.qualified_name,
                        "kind": source_symbol.kind.value,
                        "depth": depth + 1,
                    }
                    frontier.append((source_symbol.name, source_symbol.qualified_name, depth + 1))

    if arguments.start_type == "file":
        path = parse_path(arguments.start, allow_root=False)
        variants = {path.as_posix(), path.value.with_suffix("").as_posix()}
        variants.update({f"./{value}" for value in tuple(variants)})
        for variant in variants:
            for relation in await services.index.find_relations_filtered(
                generation_id,
                raw_target=variant,
                limit=limit,
            ):
                key = (
                    relation.path.as_posix(),
                    relation.span.start_byte,
                    relation.raw_target,
                    relation.relation_kind.value,
                )
                if key in seen_relations:
                    continue
                seen_relations.add(key)
                impacted_files.setdefault(
                    relation.path.as_posix(),
                    {"path": relation.path.as_posix(), "depth": 1},
                )
                evidence.append(
                    relation_evidence(
                        descriptor,
                        snapshot.commit_sha,
                        relation,
                        retrieval_method="index_impact",
                    )
                )

    lines = [
        "Impact analysis evidence",
        f"start: {arguments.start} ({arguments.start_type})",
        f"impacted_files: {len(impacted_files)}",
    ]
    lines.extend(
        f"- {item['path']} depth={item['depth']}" for item in list(impacted_files.values())[:50]
    )
    lines.append(f"impacted_symbols: {len(impacted_symbols)}")
    lines.extend(
        f"- {item['name']} ({item['kind']}) depth={item['depth']}"
        for item in list(impacted_symbols.values())[:50]
    )
    summary = _structured_evidence(
        descriptor,
        snapshot.commit_sha,
        RepositoryPath(PurePosixPath("analysis/impact.md")),
        "\n".join(lines),
        "index_impact",
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "start": arguments.start,
            "start_type": arguments.start_type,
            "impacted_files": list(impacted_files.values()),
            "impacted_symbols": list(impacted_symbols.values()),
            "count": len(impacted_files) + len(impacted_symbols),
        },
        evidence=(summary, *evidence[:50]),
        truncated=len(impacted_files) + len(impacted_symbols) >= limit,
    )


async def generate_mermaid(
    services: ToolServices, context: ToolContext, arguments: GenerateMermaidInput
) -> ToolResult:
    """Render bounded graph traversal output as a Mermaid flowchart."""
    graph = await traverse_graph(
        services,
        context,
        TraverseGraphInput(
            repository_id=arguments.repository_id,
            start=arguments.start,
            start_type=arguments.start_type,
            direction=arguments.direction,
            edge_kinds=arguments.edge_kinds,
            max_depth=arguments.max_depth,
            limit=arguments.limit,
        ),
    )
    payload = graph.payload
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    ids: dict[str, str] = {}
    for index, node in enumerate(nodes):
        if isinstance(node, dict) and isinstance(node.get("ref"), str):
            ids[node["ref"]] = f"n{index}"
    lines = ["flowchart LR"]
    for node in nodes:
        if not isinstance(node, dict) or node.get("ref") not in ids:
            continue
        label = str(node.get("label", node.get("ref", ""))).replace('"', "'").replace("\n", " ")
        label = label.replace("[", "(").replace("]", ")")
        lines.append(f'  {ids[node["ref"]]}["{label[:120]}"]')
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = ids.get(str(edge.get("from")))
        target = ids.get(str(edge.get("to")))
        if source is None or target is None:
            continue
        lines.append(f"  {source} -->|{edge.get('kind', 'edge')}| {target}")
    mermaid = "\n".join(lines)[:_MAX_MERMAID_CHARS]
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, _generation_id = await require_snapshot(services, descriptor)
    evidence = _structured_evidence(
        descriptor,
        snapshot.commit_sha,
        RepositoryPath(PurePosixPath("analysis/graph.mmd")),
        mermaid,
        "graph_mermaid",
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "start": arguments.start,
            "mermaid": mermaid,
            "nodes": len(ids),
            "edges": len(edges),
        },
        evidence=(*graph.evidence[:5], evidence),
        truncated=graph.truncated,
    )


async def review_pull_request(
    services: ToolServices, context: ToolContext, arguments: ReviewPullRequestInput
) -> ToolResult:
    """Combine GitHub pull request context with indexed impact analysis."""
    if services.github is None:
        raise NotFoundError("GitHub integration is not enabled.")
    descriptor = resolve_repository(services, context, arguments.repository_id)
    snapshot, generation_id = await require_snapshot(services, descriptor)
    ref = _repository_ref(services, arguments.repository)
    number = int(arguments.number)
    pull = await services.github.get_pull_request(ref, number)
    diff = await services.github.get_pull_request_diff(ref, number)
    changed = list(dict.fromkeys(_DIFF_PATH.findall(diff.diff)))[:20]
    impacted: dict[str, list[str]] = {}
    for changed_path in changed:
        try:
            path = RepositoryPath(PurePosixPath(changed_path))
        except ValueError:
            continue
        symbols = await services.index.list_symbols(generation_id, path=path, limit=40)
        for symbol in symbols:
            relations = await services.index.find_relations_filtered(
                generation_id,
                raw_target=symbol.name,
                limit=20,
            )
            for relation in relations:
                impacted.setdefault(relation.path.as_posix(), []).append(symbol.name)
    lines = [
        f"Pull request review context #{pull.number}: {pull.title}",
        f"state: {pull.state} head: {pull.head_sha} base: {pull.base_sha}",
        f"changes: +{pull.additions}/-{pull.deletions} in {pull.changed_files} files",
        f"changed_files: {', '.join(changed) or 'unknown'}",
        f"impacted_files: {', '.join(sorted(impacted)[:30]) or 'none detected'}",
        "",
        diff.diff[:6_000],
    ]
    excerpt = "\n".join(lines)
    evidence = EvidenceCandidate.create(
        kind=EvidenceKind.PULL_REQUEST,
        repository_id=descriptor.repository_id,
        commit_sha=pull.head_sha,
        path=RepositoryPath(PurePosixPath(f"github/{ref.full_name}/pull/{number}/review.md")),
        start_line=1,
        end_line=max(1, len(excerpt.splitlines())),
        excerpt=excerpt[:7_500],
        retrieval_method="github_pr_review",
        external_url=pull.url,
    )
    return ToolResult(
        {
            "repository_id": descriptor.repository_id.value,
            "commit_sha": snapshot.commit_sha,
            "pull_request": {
                "number": pull.number,
                "title": pull.title,
                "state": pull.state,
                "url": pull.url,
                "head_sha": pull.head_sha,
                "base_sha": pull.base_sha,
                "changed_files": changed,
            },
            "impacted_files": {path: sorted(set(names)) for path, names in impacted.items()},
            "diff": diff.diff,
            "review_notes": [
                f"{len(changed)} changed file(s); {len(impacted)} impacted file(s).",
                "Review the diff and impacted references for regressions.",
            ],
        },
        evidence=(evidence,),
    )
