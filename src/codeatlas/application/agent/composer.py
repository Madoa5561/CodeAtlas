"""Channel-neutral response composition with verified citations."""

from __future__ import annotations

from codeatlas.application.agent._text import (
    analyze,
    is_japanese,
    localized,
    one_line,
    truncate_text,
)
from codeatlas.domain.agent_models import (
    AgentLimits,
    EvidenceCandidate,
    QueryContext,
    RankedEvidenceSet,
    ReasoningInference,
    ReasoningResult,
)
from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    Citation,
    InteractionCapabilities,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)


class CitationComposer:
    """Render reasoning as canonical blocks without introducing new facts."""

    async def compose(
        self,
        context: QueryContext,
        reasoning: ReasoningResult,
        ranked: RankedEvidenceSet,
        capabilities: InteractionCapabilities,
        limits: AgentLimits,
    ) -> CanonicalResponse:
        """Compose a locale-aware cited answer within presentation limits."""
        evidence_by_id = {item.evidence.evidence_id: item.evidence for item in ranked.evidence}
        selected = self._select_citations(reasoning, ranked, capabilities, limits)
        citation_map = {evidence.evidence_id: _to_citation(evidence) for evidence in selected}
        selected_ids = set(citation_map)
        methods = _search_methods(ranked)
        facts = [
            fact
            for fact in reasoning.facts
            if any(evidence_id in evidence_by_id for evidence_id in fact.evidence_ids)
        ]
        fact_entries: list[tuple[str, tuple[str, ...]]] = []
        for fact in facts:
            cited = [
                evidence_id for evidence_id in fact.evidence_ids if evidence_id in selected_ids
            ]
            if not cited:
                continue
            primary = evidence_by_id[cited[0]]
            text = one_line(fact.text, limit=600)
            if not text:
                continue
            fact_entries.append((f"- {text} {_location(primary)}", tuple(cited)))
        inference_entries: list[tuple[str, tuple[str, ...]]] = []
        for inference in reasoning.inferences:
            cited = [
                evidence_id for evidence_id in inference.evidence_ids if evidence_id in selected_ids
            ]
            text = one_line(inference.text, limit=500)
            if not text:
                continue
            inference_entries.append((_inference_line(context, text, inference), tuple(cited)))
        uncertainty_entries = [
            one_line(uncertainty, limit=500)
            for uncertainty in reasoning.uncertainties
            if uncertainty.strip()
        ]
        citation_entries = [_citation_line(evidence) for evidence in selected]
        citation_header = _citation_header(context, selected)
        follow_up_entries = [f"- {gap.description}" for gap in reasoning.gaps]
        has_evidence = bool(evidence_by_id)
        if has_evidence:
            status = (
                ResponseStatus.PARTIAL if not fact_entries or reasoning.gaps else ResponseStatus.OK
            )
        elif _needs_clarification(context):
            status = ResponseStatus.NEEDS_CLARIFICATION
        else:
            status = ResponseStatus.PARTIAL

        blocks = self._compose_blocks(
            context=context,
            reasoning=reasoning,
            status=status,
            has_evidence=has_evidence,
            fact_entries=fact_entries,
            inference_entries=inference_entries,
            uncertainty_entries=uncertainty_entries,
            citation_entries=citation_entries,
            citation_header=citation_header,
            methods=methods,
            follow_up_entries=follow_up_entries,
            budget=limits.max_response_chars,
        )
        return CanonicalResponse(
            status=status,
            blocks=blocks,
            citations=tuple(citation_map.values()),
            confidence=max(0.0, min(1.0, reasoning.confidence)),
            uncertainties=tuple(reasoning.uncertainties[:5]),
            search_methods=methods,
            follow_ups=tuple(gap.description for gap in reasoning.gaps[:3]),
        )

    def _select_citations(
        self,
        reasoning: ReasoningResult,
        ranked: RankedEvidenceSet,
        capabilities: InteractionCapabilities,
        limits: AgentLimits,
    ) -> tuple[EvidenceCandidate, ...]:
        evidence_by_id = {item.evidence.evidence_id: item.evidence for item in ranked.evidence}
        ordered: list[EvidenceCandidate] = []
        seen: set[str] = set()

        def add(evidence_id: str) -> None:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None or evidence.evidence_id in seen:
                return
            seen.add(evidence.evidence_id)
            ordered.append(evidence)

        for fact in reasoning.facts:
            for evidence_id in fact.evidence_ids:
                add(evidence_id)
        for inference in reasoning.inferences:
            for evidence_id in inference.evidence_ids:
                add(evidence_id)

        cited_count = len(ordered)
        # Keep only cited evidence plus a small supporting set. Answers with many
        # verified facts should not turn every retrieval hit into a citation.
        limit = min(capabilities.max_citations, 8)
        target = cited_count if cited_count >= 3 else min(3, limit)
        for item in ranked.evidence:
            if len(ordered) >= target:
                break
            add(item.evidence.evidence_id)
        return tuple(ordered[:limit])

    def _compose_blocks(
        self,
        *,
        context: QueryContext,
        reasoning: ReasoningResult,
        status: ResponseStatus,
        has_evidence: bool,
        fact_entries: list[tuple[str, tuple[str, ...]]],
        inference_entries: list[tuple[str, tuple[str, ...]]],
        uncertainty_entries: list[str],
        citation_entries: list[str],
        citation_header: str,
        methods: tuple[str, ...],
        follow_up_entries: list[str],
        budget: int,
    ) -> tuple[ResponseBlock, ...]:
        conclusion_text = one_line(reasoning.conclusion, limit=2_000)
        if not has_evidence:
            nothing_found = localized(
                context.locale,
                ja="該当する根拠は見つかりませんでした。",
                en="No matching repository evidence was found.",
            )
            parts = [nothing_found, conclusion_text]
            if methods:
                parts.append(
                    localized(
                        context.locale,
                        ja="試した検索方法: " + ", ".join(methods),
                        en="Search methods tried: " + ", ".join(methods),
                    )
                )
            parts.extend(uncertainty_entries)
            blocks: list[ResponseBlock] = [
                ResponseBlock(kind=ResponseBlockKind.TEXT, text="\n".join(p for p in parts if p))
            ]
            return _trim_blocks(blocks, budget)
        confidence_label = _confidence_label(context, reasoning.confidence)
        conclusion_parts = [conclusion_text]
        if status is ResponseStatus.PARTIAL:
            conclusion_parts.append(
                localized(
                    context.locale,
                    ja="一部の情報のみ確認できました。",
                    en="Only part of the requested information could be verified.",
                )
            )
        conclusion_parts.append(
            localized(
                context.locale,
                ja=f"信頼度: {confidence_label}",
                en=f"Confidence: {confidence_label}",
            )
        )
        blocks = [
            ResponseBlock(
                kind=ResponseBlockKind.TEXT,
                text="\n\n".join(part for part in conclusion_parts if part),
            )
        ]
        if fact_entries:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.BULLETS,
                    text="\n".join([_facts_header(context), *(entry[0] for entry in fact_entries)]),
                    citation_ids=tuple(
                        dict.fromkeys(
                            citation_id for entry in fact_entries for citation_id in entry[1]
                        )
                    ),
                )
            )
        if inference_entries:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.BULLETS,
                    text="\n".join(
                        [_inferences_header(context), *(entry[0] for entry in inference_entries)]
                    ),
                    citation_ids=tuple(
                        dict.fromkeys(
                            citation_id for entry in inference_entries for citation_id in entry[1]
                        )
                    ),
                )
            )
        if uncertainty_entries:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.BULLETS,
                    text="\n".join([_uncertainties_header(context), *uncertainty_entries]),
                )
            )
        if citation_entries:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.BULLETS,
                    text="\n".join([citation_header, *citation_entries]),
                )
            )
        if methods:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.TEXT,
                    text=localized(
                        context.locale,
                        ja="検索方法: " + ", ".join(methods),
                        en="Search methods: " + ", ".join(methods),
                    ),
                )
            )
        if follow_up_entries:
            blocks.append(
                ResponseBlock(
                    kind=ResponseBlockKind.BULLETS,
                    text="\n".join([_follow_ups_header(context), *follow_up_entries]),
                )
            )
        return _trim_blocks(blocks, budget)


def _trim_blocks(blocks: list[ResponseBlock], budget: int) -> tuple[ResponseBlock, ...]:
    """Drop lower-value blocks first and never leave the response over budget."""
    trimmed = list(blocks)
    while len(trimmed) > 1 and _blocks_length(trimmed) > budget:
        removed = False
        for index in range(len(trimmed) - 1, 0, -1):
            if trimmed[index].kind is not ResponseBlockKind.TEXT or index != 0:
                trimmed.pop(index)
                removed = True
                break
        if not removed:
            break
    if len(trimmed) == 1 and len(trimmed[0].text) > budget:
        block = trimmed[0]
        text = truncate_text(block.text, limit=max(budget, 1))
        trimmed[0] = ResponseBlock(
            kind=block.kind,
            text=text,
            language=block.language,
            citation_ids=block.citation_ids,
        )
    return tuple(trimmed)


def _blocks_length(blocks: list[ResponseBlock]) -> int:
    return sum(len(block.text) for block in blocks) + 2 * max(0, len(blocks) - 1)


def _to_citation(evidence: EvidenceCandidate) -> Citation:
    return Citation(
        citation_id=evidence.evidence_id,
        repository_id=evidence.repository_id.value,
        commit_sha=evidence.commit_sha,
        path=evidence.path.as_posix() or ".",
        start_line=evidence.start_line,
        end_line=evidence.end_line,
        retrieval_method=evidence.retrieval_method,
        symbol_id=evidence.symbol_id.value if evidence.symbol_id is not None else None,
        symbol_name=evidence.symbol_name,
    )


def _location(evidence: EvidenceCandidate) -> str:
    path = evidence.path.as_posix() or "."
    if evidence.end_line != evidence.start_line:
        return f"[{path}:{evidence.start_line}-{evidence.end_line}]"
    return f"[{path}:{evidence.start_line}]"


def _search_methods(ranked: RankedEvidenceSet) -> tuple[str, ...]:
    methods: list[str] = []
    for item in ranked.evidence:
        method = item.evidence.retrieval_method
        if method not in methods:
            methods.append(method)
    return tuple(methods)


def _needs_clarification(context: QueryContext) -> bool:
    signals = analyze(context.query)
    return not signals.has_focus and not signals.wants_intent_tool and len(signals.terms) <= 3


def _confidence_label(context: QueryContext, confidence: float) -> str:
    ja = is_japanese(context.locale)
    if confidence >= 0.75:
        return "高" if ja else "high"
    if confidence >= 0.45:
        return "中" if ja else "medium"
    return "低" if ja else "low"


def _inference_line(
    context: QueryContext,
    text: str,
    inference: ReasoningInference,
) -> str:
    label = localized(context.locale, ja="推測", en="inference")
    confidence = f"{inference.confidence:.2f}"
    return f"- ({label}, {confidence}) {text}"


def _facts_header(context: QueryContext) -> str:
    return localized(context.locale, ja="確認できた事実:", en="Verified facts:")


def _inferences_header(context: QueryContext) -> str:
    return localized(context.locale, ja="推測・未検証:", en="Inferences (not verified):")


def _uncertainties_header(context: QueryContext) -> str:
    return localized(context.locale, ja="不確実な点:", en="Uncertainties:")


def _citations_header(context: QueryContext) -> str:
    return localized(context.locale, ja="引用元:", en="Citations:")


def _follow_ups_header(context: QueryContext) -> str:
    return localized(context.locale, ja="追加調査候補:", en="Suggested follow-ups:")


def _citation_header(context: QueryContext, selected: tuple[EvidenceCandidate, ...]) -> str:
    """Return a compact citation header with one provenance per repository/SHA."""
    provenance = list(
        dict.fromkeys(
            f"{evidence.repository_id.value}@{evidence.commit_sha}" for evidence in selected
        )
    )
    header = _citations_header(context)
    if not provenance:
        return header
    if len(provenance) == 1:
        return f"{header} {provenance[0]}"
    return f"{header}\n" + "\n".join(f"- {value}" for value in provenance)


def _citation_line(evidence: EvidenceCandidate) -> str:
    path = evidence.path.as_posix() or "."
    if evidence.end_line != evidence.start_line:
        location = f"{path}:{evidence.start_line}-{evidence.end_line}"
    else:
        location = f"{path}:{evidence.start_line}"
    details: list[str] = []
    if evidence.symbol_name:
        details.append(evidence.symbol_name)
    details.append(evidence.retrieval_method)
    return f"- {location} ({' / '.join(details)})"
