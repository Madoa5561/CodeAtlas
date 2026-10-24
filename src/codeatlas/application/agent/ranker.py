"""Deterministic evidence ranking for the agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.application.agent._text import QuerySignals, analyze
from codeatlas.domain.agent_models import (
    AgentLimits,
    EvidenceCandidate,
    EvidenceKind,
    QueryContext,
    RankedEvidence,
    RankedEvidenceSet,
    RetrievalResult,
)

_KIND_AUTHORITY: dict[EvidenceKind, float] = {
    EvidenceKind.SYMBOL: 1.5,
    EvidenceKind.FILE_EXCERPT: 1.2,
    EvidenceKind.REFERENCE: 1.1,
    EvidenceKind.IMPORT: 1.0,
    EvidenceKind.COMMIT: 0.8,
    EvidenceKind.TREE_ENTRY: 0.5,
}
_METHOD_DIVERSITY_BONUS = 0.75
_MAX_LEXICAL_BONUS = 3.0


@dataclass(slots=True)
class _Scored:
    evidence: EvidenceCandidate
    base: float
    signals: list[str] = field(default_factory=list)


class HeuristicEvidenceRanker:
    """Score evidence using exact symbols, paths, lexical overlap, and provenance."""

    async def rank(
        self,
        context: QueryContext,
        candidates: RetrievalResult,
        limits: AgentLimits,
    ) -> RankedEvidenceSet:
        """Return deterministic, diverse, bounded ranked evidence."""
        signals = analyze(context.query)
        unique = _dedupe_spans(candidates.candidates)
        scored = [self._score(evidence, signals) for evidence in unique]
        ordered = sorted(scored, key=lambda item: (-item.base, item.evidence.evidence_id))
        selected: list[_Scored] = []
        selected_scores: list[float] = []
        remaining = list(ordered)
        chosen_methods: set[str] = set()
        while remaining and len(selected) < limits.max_evidence:
            pick_index = 0
            pick_value = float("-inf")
            for index, item in enumerate(remaining):
                bonus = (
                    0.0
                    if item.evidence.retrieval_method in chosen_methods
                    else (_METHOD_DIVERSITY_BONUS)
                )
                value = item.base + bonus
                if value > pick_value + 1e-9:
                    pick_index = index
                    pick_value = value
            picked = remaining.pop(pick_index)
            bonus = (
                0.0
                if picked.evidence.retrieval_method in chosen_methods
                else _METHOD_DIVERSITY_BONUS
            )
            chosen_methods.add(picked.evidence.retrieval_method)
            item_signals = list(picked.signals)
            if bonus:
                item_signals.append("method_diversity")
            selected.append(_Scored(picked.evidence, picked.base, item_signals))
            selected_scores.append(round(picked.base + bonus, 3))
        ordered_selection = sorted(
            zip(selected, selected_scores, strict=True),
            key=lambda pair: (-pair[1], pair[0].evidence.evidence_id),
        )
        ranked = tuple(
            RankedEvidence(
                evidence=item.evidence,
                score=score,
                rank=rank,
                signals=tuple(item.signals),
            )
            for rank, (item, score) in enumerate(ordered_selection, start=1)
        )
        return RankedEvidenceSet(
            evidence=ranked,
            omitted_count=max(0, len(unique) - len(ranked)),
        )

    def _score(self, evidence: EvidenceCandidate, signals: QuerySignals) -> _Scored:
        score = 0.0
        matched: list[str] = []
        identifiers = tuple(identifier.casefold() for identifier in signals.identifiers)
        phrase = signals.search_phrase.casefold() if signals.search_phrase else ""
        path_text = evidence.path.as_posix().casefold()
        basename = path_text.rsplit("/", 1)[-1]
        symbol = (evidence.symbol_name or "").casefold()
        excerpt = evidence.excerpt.casefold()

        if symbol:
            for identifier in identifiers:
                if symbol == identifier:
                    score += 6.0
                    matched.append("exact_symbol")
                    break
                if symbol.endswith("." + identifier):
                    score += 4.5
                    matched.append("qualified_symbol")
                    break
        if not matched and symbol and phrase and phrase == symbol:
            score += 5.0
            matched.append("phrase_symbol")

        for candidate_path in (*signals.files, *signals.globs, *signals.directories):
            normalized = candidate_path.casefold().rstrip("/")
            if not normalized:
                continue
            if path_text == normalized:
                score += 5.0
                matched.append("path_match")
                break
            if basename == normalized or basename.startswith(normalized + "."):
                score += 4.0
                matched.append("filename_match")
                break
            if candidate_path.endswith("/") or "/" in candidate_path:
                if path_text.startswith(normalized + "/"):
                    score += 2.5
                    matched.append("path_prefix")
                    break

        if phrase and len(phrase) >= 3 and phrase in excerpt:
            score += 2.0
            matched.append("phrase_in_excerpt")
        lexical = 0.0
        for term in signals.terms:
            if term and term in excerpt:
                lexical += 0.5
        if lexical:
            score += min(lexical, _MAX_LEXICAL_BONUS)
            matched.append("lexical_overlap")
        for identifier in identifiers:
            if identifier and identifier in excerpt:
                score += 1.5
                matched.append("identifier_in_excerpt")
                break

        score += _KIND_AUTHORITY.get(evidence.kind, 0.5)
        if evidence.verified:
            score += 1.0
            matched.append("verified")
        return _Scored(evidence=evidence, base=round(score, 3), signals=matched)


def _dedupe_spans(candidates: tuple[EvidenceCandidate, ...]) -> tuple[EvidenceCandidate, ...]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            not item.verified,
            -item.confidence,
            item.evidence_id,
        ),
    )
    seen_ids: set[str] = set()
    seen_spans: set[tuple[str, str, str, int, int, str]] = set()
    unique: list[EvidenceCandidate] = []
    for evidence in ordered:
        if evidence.evidence_id in seen_ids:
            continue
        span_key = (
            evidence.repository_id.value,
            evidence.commit_sha,
            evidence.path.as_posix(),
            evidence.start_line,
            evidence.end_line,
            evidence.kind.value,
        )
        if span_key in seen_spans:
            continue
        seen_ids.add(evidence.evidence_id)
        seen_spans.add(span_key)
        unique.append(evidence)
    return tuple(unique)
