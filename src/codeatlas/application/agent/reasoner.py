"""Grounded reasoning over verified evidence with a safe extractive fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from codeatlas.application.agent._text import localized, one_line, strip_inline_citations
from codeatlas.application.agent.diagnostics import (
    DegradedReason,
    ReasonerDiagnostics,
)
from codeatlas.application.agent.prompts import build_reasoning_request
from codeatlas.domain.agent_models import (
    AgentLimits,
    EvidenceCandidate,
    QueryContext,
    RankedEvidenceSet,
    ReasoningFact,
    ReasoningInference,
    ReasoningResult,
    RetrievalGap,
)
from codeatlas.domain.agent_ports import ModelProviderPort
from codeatlas.domain.errors import AgentError

logger = logging.getLogger(__name__)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_MAX_FACT_CHARS = 4_000
_MAX_CONCLUSION_CHARS = 4_000
_MAX_UNCERTAINTY_CHARS = 1_000
_MAX_GAP_CHARS = 500


@dataclass(slots=True)
class _ParsedModelReasoning:
    conclusion: str
    facts: list[ReasoningFact] = field(default_factory=list)
    inferences: list[ReasoningInference] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    confidence: float = 0.5
    gaps: list[RetrievalGap] = field(default_factory=list)
    raw_fact_count: int = 0


class GroundedReasoner:
    """Use a model when available and fall back to deterministic extraction."""

    def __init__(
        self,
        *,
        provider: ModelProviderPort | None = None,
        fallback_without_model: bool = True,
        max_evidence_in_prompt: int = 12,
        max_excerpt_chars: int = 1_200,
        max_envelope_chars: int = 16_000,
        max_facts: int = 8,
    ) -> None:
        if not 1 <= max_evidence_in_prompt <= 50:
            raise ValueError("invalid prompt evidence limit")
        if not 80 <= max_excerpt_chars <= 8_000:
            raise ValueError("invalid prompt excerpt limit")
        if not 1_000 <= max_envelope_chars <= 80_000:
            raise ValueError("invalid prompt envelope limit")
        if not 1 <= max_facts <= 20:
            raise ValueError("invalid extracted fact limit")
        self._provider = provider
        self._fallback_without_model = fallback_without_model
        self._max_evidence_in_prompt = max_evidence_in_prompt
        self._max_excerpt_chars = max_excerpt_chars
        self._max_envelope_chars = max_envelope_chars
        self._max_facts = max_facts
        # Context-local so concurrent requests never observe each other's diagnostics.
        self._diagnostics: ContextVar[ReasonerDiagnostics | None] = ContextVar(
            f"codeatlas_reasoner_diagnostics_{id(self)}",
            default=None,
        )

    @property
    def last_diagnostics(self) -> ReasonerDiagnostics:
        """Return diagnostics from the most recent reason call in this task."""
        return self._diagnostics.get() or ReasonerDiagnostics()

    def _set_diagnostics(self, diagnostics: ReasonerDiagnostics) -> None:
        self._diagnostics.set(diagnostics)

    async def reason(
        self,
        context: QueryContext,
        ranked: RankedEvidenceSet,
        limits: AgentLimits,
    ) -> ReasoningResult:
        """Produce verified facts without ever executing tools."""
        self._set_diagnostics(ReasonerDiagnostics())
        if not ranked.evidence:
            return self._empty_result(context)
        provider = self._provider
        if provider is None or not provider.enabled:
            if not self._fallback_without_model:
                self._set_diagnostics(
                    ReasonerDiagnostics(
                        degraded_reasons=(DegradedReason.MODEL_REQUIRED_BUT_UNAVAILABLE.value,)
                    )
                )
                raise AgentError("The language model is required but unavailable.")
            self._set_diagnostics(
                ReasonerDiagnostics(degraded_reasons=(DegradedReason.MODEL_DISABLED.value,))
            )
            return self._extractive_result(context, ranked)
        request = build_reasoning_request(
            context,
            ranked,
            limits,
            max_evidence=self._max_evidence_in_prompt,
            max_excerpt_chars=self._max_excerpt_chars,
            max_envelope_chars=self._max_envelope_chars,
        )
        try:
            response = await provider.complete(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "agent_reasoner_model_failed",
                extra={"error_type": type(error).__name__, "provider_id": provider.provider_id},
            )
            self._set_diagnostics(
                ReasonerDiagnostics(
                    model_id=provider.model_id,
                    degraded_reasons=(DegradedReason.MODEL_ERROR.value,),
                    model_calls=1,
                )
            )
            return self._extractive_result(context, ranked)
        model_id = response.model_id or provider.model_id
        parsed = _parse_model_reasoning(response.content, ranked)
        if parsed is None:
            self._set_diagnostics(
                ReasonerDiagnostics(
                    model_id=model_id,
                    degraded_reasons=(DegradedReason.MODEL_INVALID_JSON.value,),
                    model_calls=1,
                )
            )
            return self._extractive_result(context, ranked)
        if parsed.raw_fact_count > 0 and not parsed.facts:
            self._set_diagnostics(
                ReasonerDiagnostics(
                    model_id=model_id,
                    degraded_reasons=(DegradedReason.MODEL_UNVERIFIED_CITATIONS.value,),
                    model_calls=1,
                )
            )
            return self._extractive_result(context, ranked)
        self._set_diagnostics(ReasonerDiagnostics(model_id=model_id, model_calls=1))
        return ReasoningResult(
            conclusion=parsed.conclusion,
            facts=tuple(parsed.facts),
            inferences=tuple(parsed.inferences),
            uncertainties=tuple(parsed.uncertainties),
            confidence=parsed.confidence,
            gaps=tuple(parsed.gaps),
        )

    def _empty_result(self, context: QueryContext) -> ReasoningResult:
        return ReasoningResult(
            conclusion=localized(
                context.locale,
                ja="質問に関連する根拠は見つかりませんでした。",
                en="No repository evidence related to the question was found.",
            ),
            facts=(),
            inferences=(),
            uncertainties=(
                localized(
                    context.locale,
                    ja="検索した範囲では、質問に答えるための検証済みの根拠がありませんでした。",
                    en=(
                        "The searched scope did not contain verified evidence "
                        "answering the question."
                    ),
                ),
            ),
            confidence=0.0,
            gaps=(),
        )

    def _extractive_result(
        self,
        context: QueryContext,
        ranked: RankedEvidenceSet,
    ) -> ReasoningResult:
        facts = self._extracted_facts(context, ranked)
        verified_count = sum(1 for item in ranked.evidence if item.evidence.verified)
        confidence = 0.35 + 0.05 * len(facts)
        if verified_count == len(ranked.evidence):
            confidence += 0.05
        confidence = round(min(confidence, 0.72), 3)
        uncertainties = list(self._extraction_uncertainties(context, ranked))
        conclusion = localized(
            context.locale,
            ja=f"検索結果から {len(facts)} 件の検証済みの根拠を確認しました。",
            en=f"Verified {len(facts)} evidence excerpts from the search results.",
        )
        return ReasoningResult(
            conclusion=conclusion,
            facts=tuple(facts),
            inferences=(),
            uncertainties=tuple(uncertainties),
            confidence=confidence,
            gaps=(),
        )

    def _extracted_facts(
        self,
        context: QueryContext,
        ranked: RankedEvidenceSet,
    ) -> list[ReasoningFact]:
        facts: list[ReasoningFact] = []
        for item in ranked.evidence[: self._max_facts]:
            evidence = item.evidence
            text = self._describe_evidence(context, evidence)
            if not text:
                continue
            facts.append(
                ReasoningFact(
                    text=text[:_MAX_FACT_CHARS],
                    evidence_ids=(evidence.evidence_id,),
                )
            )
        return facts

    @staticmethod
    def _describe_evidence(context: QueryContext, evidence: EvidenceCandidate) -> str:
        path = evidence.path.as_posix() or "."
        location = f"{path}:{evidence.start_line}"
        if evidence.end_line != evidence.start_line:
            location = f"{path}:{evidence.start_line}-{evidence.end_line}"
        if evidence.symbol_name:
            return localized(
                context.locale,
                ja=f"`{evidence.symbol_name}` が {location} で確認できます。",
                en=f"`{evidence.symbol_name}` is found at {location}.",
            )
        excerpt_line = ""
        for line in evidence.excerpt.splitlines():
            if line.strip():
                excerpt_line = one_line(line, limit=180)
                break
        if not excerpt_line:
            return ""
        return localized(
            context.locale,
            ja=f'{location} に次の記述があります: "{excerpt_line}"',
            en=f'{location} contains the following text: "{excerpt_line}"',
        )

    @staticmethod
    def _extraction_uncertainties(
        context: QueryContext,
        ranked: RankedEvidenceSet,
    ) -> tuple[str, ...]:
        uncertainties: list[str] = []
        if not all(item.evidence.verified for item in ranked.evidence):
            uncertainties.append(
                localized(
                    context.locale,
                    ja="一部の検索結果は検証されていません。",
                    en="Some search results are not verified.",
                )
            )
        if not uncertainties:
            uncertainties.append(
                localized(
                    context.locale,
                    ja="検索結果の抜粋のみに基づく回答であり、完全な動作確認ではありません。",
                    en="This answer is based only on retrieved excerpts, not on full validation.",
                )
            )
        return tuple(uncertainties)


def _parse_model_reasoning(
    content: str,
    ranked: RankedEvidenceSet,
) -> _ParsedModelReasoning | None:
    payload = _decode_json_object(content)
    if payload is None:
        return None
    conclusion = _clean_string(payload.get("conclusion"), _MAX_CONCLUSION_CHARS)
    if not conclusion:
        return None
    evidence_ids = {item.evidence.evidence_id for item in ranked.evidence}
    raw_facts = payload.get("facts", [])
    if not isinstance(raw_facts, list):
        return None
    facts: list[ReasoningFact] = []
    for raw_fact in raw_facts[:12]:
        if not isinstance(raw_fact, dict):
            continue
        text = _clean_string(raw_fact.get("text"), _MAX_FACT_CHARS)
        citations = _valid_evidence_ids(raw_fact.get("evidence_ids"), evidence_ids)
        if not text or not citations:
            continue
        facts.append(ReasoningFact(text=text, evidence_ids=citations))
    inferences: list[ReasoningInference] = []
    raw_inferences = payload.get("inferences", [])
    if isinstance(raw_inferences, list):
        for raw_inference in raw_inferences[:8]:
            if not isinstance(raw_inference, dict):
                continue
            text = _clean_string(raw_inference.get("text"), _MAX_FACT_CHARS)
            if not text:
                continue
            confidence = _bounded_float(raw_inference.get("confidence"), default=0.5)
            inferences.append(
                ReasoningInference(
                    text=text,
                    evidence_ids=_valid_evidence_ids(
                        raw_inference.get("evidence_ids"), evidence_ids
                    ),
                    confidence=confidence,
                )
            )
    uncertainties = _string_list(payload.get("uncertainties"), _MAX_UNCERTAINTY_CHARS, 6)
    confidence = _bounded_float(payload.get("confidence"), default=0.55)
    gaps: list[RetrievalGap] = []
    raw_gaps = payload.get("gaps", [])
    if isinstance(raw_gaps, list):
        for raw_gap in raw_gaps[:3]:
            if not isinstance(raw_gap, dict):
                continue
            description = _clean_string(raw_gap.get("description"), _MAX_GAP_CHARS)
            if not description:
                continue
            suggested = _suggested_tools(raw_gap.get("suggested_tools"))
            gaps.append(RetrievalGap(description=description, suggested_tools=suggested))
    return _ParsedModelReasoning(
        conclusion=conclusion,
        facts=facts,
        inferences=inferences,
        uncertainties=uncertainties,
        confidence=confidence,
        gaps=gaps,
        raw_fact_count=len(raw_facts),
    )


def _decode_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text or len(text) > 100_000:
        return None
    candidates = [text]
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidates.append("\n".join(lines[1:-1]).strip())
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1])
    for candidate in candidates:
        try:
            decoded: Any = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _clean_string(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = strip_inline_citations(value)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].strip()


def _string_list(value: Any, limit: int, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    results: list[str] = []
    for item in value[:max_items]:
        cleaned = _clean_string(item, limit)
        if cleaned:
            results.append(cleaned)
    return results


def _valid_evidence_ids(value: Any, known: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    valid: list[str] = []
    for item in value:
        if isinstance(item, str) and item in known and item not in valid:
            valid.append(item)
        if len(valid) >= 4:
            break
    return tuple(valid)


def _bounded_float(value: Any, *, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(0.0, min(1.0, float(value)))


def _suggested_tools(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    tools: list[str] = []
    for item in value[:5]:
        if isinstance(item, str) and _TOOL_NAME.fullmatch(item) and item not in tools:
            tools.append(item)
    return tuple(tools)
