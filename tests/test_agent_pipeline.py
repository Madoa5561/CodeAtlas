"""End-to-end tests for the five-stage agent pipeline with fake adapters."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

import pytest

from codeatlas.application.agent import (
    AgentOrchestrator,
    CitationComposer,
    GroundedReasoner,
    HeuristicEvidenceRanker,
    HeuristicPlanner,
    ToolPlanRetriever,
)
from codeatlas.domain.agent_models import (
    AgentLimits,
    CanonicalToolCall,
    EvidenceCandidate,
    EvidenceKind,
    ModelRequest,
    ModelResponse,
    QueryContext,
    RankedEvidenceSet,
    RetrievalResult,
    ToolDefinition,
    ToolErrorCategory,
    ToolOutcome,
)
from codeatlas.domain.conversation_models import ConversationId
from codeatlas.domain.interaction_models import (
    InteractionCapabilities,
    ResponseBlockKind,
    ResponseStatus,
)
from codeatlas.domain.repository_models import RepositoryId, RepositoryPath

SHA = "a" * 40
REPO = RepositoryId("repo_" + "b" * 32)
_STRING = {"type": "string"}
_EVIDENCE_ID = re.compile(r"id=(ev_[0-9a-f]{24})")


def _tool(
    name: str,
    properties: dict[str, Any],
    required: Sequence[str] = (),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}.",
        input_schema_json=json.dumps(
            {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            }
        ),
    )


def build_catalog() -> tuple[ToolDefinition, ...]:
    integer = {"type": "integer"}
    return (
        _tool(
            "search_text", {"query": _STRING, "path_prefix": _STRING, "limit": integer}, ("query",)
        ),
        _tool("search_code", {"query": _STRING, "limit": integer}, ("query",)),
        _tool("find_symbol", {"name": _STRING}, ()),
        _tool("find_definition", {"name": _STRING}, ("name",)),
        _tool("find_references", {"name": _STRING}, ("name",)),
        _tool("find_callers", {"name": _STRING}, ("name",)),
        _tool("read_file", {"path": _STRING}, ("path",)),
        _tool("get_file_history", {"path": _STRING}, ("path",)),
        _tool("list_directory", {"path": _STRING}, ()),
        _tool("search_commits", {"query": _STRING, "path": _STRING}, ()),
        _tool("get_commit", {"revision": _STRING}, ()),
        _tool("get_diff", {"revision": _STRING}, ()),
        _tool("list_repositories", {}, ()),
    )


def make_evidence(
    *,
    kind: EvidenceKind = EvidenceKind.FILE_EXCERPT,
    path: str = "src/app.ts",
    start_line: int = 10,
    end_line: int = 12,
    excerpt: str = "export function parseConfig() { return {}; }",
    method: str = "search_text",
    symbol_name: str | None = None,
    verified: bool = True,
) -> EvidenceCandidate:
    return EvidenceCandidate.create(
        kind=kind,
        repository_id=REPO,
        commit_sha=SHA,
        path=RepositoryPath(PurePosixPath(path)),
        start_line=start_line,
        end_line=end_line,
        excerpt=excerpt,
        retrieval_method=method,
        symbol_name=symbol_name,
        verified=verified,
    )


class FakeToolExecutor:
    """Deterministic ToolExecutorPort double with synthetic pinned evidence."""

    def __init__(
        self,
        *,
        empty: bool = False,
        failing: Sequence[str] = (),
        delay: float = 0.0,
        evidence: Sequence[EvidenceCandidate] | None = None,
    ) -> None:
        self.calls: list[CanonicalToolCall] = []
        self._empty = empty
        self._failing = set(failing)
        self._delay = delay
        self._evidence = tuple(evidence) if evidence is not None else None

    def catalog(self) -> tuple[Any, ...]:
        return build_catalog()

    async def execute(self, call: CanonicalToolCall, context: Any) -> ToolOutcome:
        del context
        self.calls.append(call)
        if self._delay:
            await asyncio.sleep(self._delay)
        if call.tool_name in self._failing:
            return ToolOutcome(
                tool_name=call.tool_name,
                ok=False,
                error_category=ToolErrorCategory.FORBIDDEN,
                safe_message="not permitted",
            )
        if self._empty:
            return ToolOutcome(tool_name=call.tool_name, ok=True)
        evidence = self._evidence if self._evidence is not None else self._default(call.tool_name)
        return ToolOutcome(tool_name=call.tool_name, ok=True, evidence=evidence)

    def _default(self, tool_name: str) -> tuple[EvidenceCandidate, ...]:
        if tool_name in {"find_symbol", "find_definition", "find_references"}:
            return (
                make_evidence(
                    kind=EvidenceKind.SYMBOL,
                    start_line=10,
                    end_line=12,
                    excerpt="export function parseConfig() { return {}; }",
                    method=tool_name,
                    symbol_name="parseConfig",
                ),
            )
        if tool_name == "read_file":
            return (
                make_evidence(
                    start_line=1,
                    end_line=4,
                    excerpt="import { config } from './config';",
                    method="read_file",
                ),
            )
        if tool_name == "get_file_history":
            return (
                make_evidence(
                    kind=EvidenceKind.COMMIT,
                    start_line=1,
                    end_line=1,
                    excerpt="commit: rename config parser",
                    method="get_file_history",
                ),
            )
        if tool_name == "list_directory":
            return (
                make_evidence(
                    kind=EvidenceKind.TREE_ENTRY,
                    start_line=1,
                    end_line=1,
                    excerpt="src/app.ts",
                    method="list_directory",
                ),
            )
        if tool_name == "list_repositories":
            return (
                make_evidence(
                    kind=EvidenceKind.TREE_ENTRY,
                    path=".",
                    start_line=1,
                    end_line=1,
                    excerpt="repository: codeatlas",
                    method="list_repositories",
                ),
            )
        if tool_name in {"search_commits", "get_commit", "get_diff"}:
            return (
                make_evidence(
                    kind=EvidenceKind.COMMIT,
                    start_line=1,
                    end_line=1,
                    excerpt="commit: adjust config parser",
                    method=tool_name,
                ),
            )
        return (
            make_evidence(
                start_line=10,
                end_line=12,
                excerpt="export function parseConfig() { return {}; }",
                method=tool_name,
            ),
        )


class ScriptedModelProvider:
    """ModelProviderPort double returning scripted content without network access."""

    provider_id = "fake"
    model_id = "fake-model"
    enabled = True

    def __init__(self, responses: Sequence[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("unexpected model call")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ModelResponse):
            return item
        content = item(request) if callable(item) else item
        if isinstance(content, ModelResponse):
            return content
        return ModelResponse(content=str(content), model_id=self.model_id)

    async def aclose(self) -> None:
        self.closed = True


def make_context(
    query: str,
    *,
    locale: str = "ja",
    capabilities: InteractionCapabilities | None = None,
) -> QueryContext:
    return QueryContext(
        request_id="req_test_1",
        query=query,
        conversation_id=ConversationId.derive("test", "thread-1"),
        capabilities=capabilities or InteractionCapabilities.discord_thread(),
        locale=locale,
    )


def make_limits(**overrides: Any) -> AgentLimits:
    values: dict[str, Any] = {
        "max_iterations": 2,
        "max_tool_calls": 6,
        "deadline_seconds": 10.0,
        "max_evidence": 6,
        "max_plan_steps": 4,
        "max_response_chars": 6_000,
    }
    values.update(overrides)
    return AgentLimits(**values)


def build_pipeline(
    executor: FakeToolExecutor,
    provider: ScriptedModelProvider | None = None,
    *,
    fallback_without_model: bool = True,
) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=HeuristicPlanner(),
        retriever=ToolPlanRetriever(executor=executor),
        ranker=HeuristicEvidenceRanker(),
        reasoner=GroundedReasoner(
            provider=provider,
            fallback_without_model=fallback_without_model,
        ),
        composer=CitationComposer(),
    )


def evidence_ids_in(request: ModelRequest) -> list[str]:
    user_content = request.messages[-1].content
    return list(dict.fromkeys(_EVIDENCE_ID.findall(user_content)))


def grounded_payload(
    request: ModelRequest,
    *,
    fact_text: str = "`parseConfig` is declared in src/app.ts.",
    conclusion: str = "The configuration parser is defined in src/app.ts.",
    confidence: float = 0.8,
    gaps: list[dict[str, Any]] | None = None,
    extra_facts: list[dict[str, Any]] | None = None,
) -> str:
    ids = evidence_ids_in(request)
    facts: list[dict[str, Any]] = []
    if ids:
        facts.append({"text": fact_text, "evidence_ids": [ids[0]]})
    if extra_facts:
        facts.extend(extra_facts)
    return json.dumps(
        {
            "conclusion": conclusion,
            "facts": facts,
            "inferences": [],
            "uncertainties": [],
            "confidence": confidence,
            "gaps": gaps or [],
        }
    )


async def test_planner_japanese_symbol_intent_uses_catalog_tools() -> None:
    planner = HeuristicPlanner()
    context = make_context("`parseConfig` はどこで定義されていますか")
    plan = await planner.plan(context, build_catalog(), make_limits())
    names = [step.tool_name for step in plan.steps]
    catalog_names = {definition.name for definition in build_catalog()}
    assert names
    assert set(names) <= catalog_names
    assert "find_definition" in names
    assert any(step.tool_name in {"search_text", "search_code"} for step in plan.steps)
    find_step = next(step for step in plan.steps if step.tool_name == "find_definition")
    assert json.loads(find_step.arguments_json)["name"] == "parseConfig"
    assert len(plan.steps) <= make_limits().max_plan_steps


async def test_planner_english_history_and_path_intent() -> None:
    planner = HeuristicPlanner()
    context = make_context("Show the history of src/app.ts", locale="en")
    plan = await planner.plan(context, build_catalog(), make_limits())
    names = [step.tool_name for step in plan.steps]
    assert "get_file_history" in names
    history = next(step for step in plan.steps if step.tool_name == "get_file_history")
    assert json.loads(history.arguments_json) == {"path": "src/app.ts"}


async def test_planner_callers_directory_and_glob_intents() -> None:
    planner = HeuristicPlanner()
    callers = await planner.plan(
        make_context("`parseConfig` の呼び出し元を教えて"), build_catalog(), make_limits()
    )
    assert "find_callers" in [step.tool_name for step in callers.steps]
    directory = await planner.plan(
        make_context("List files in src/", locale="en"), build_catalog(), make_limits()
    )
    directory_steps = [step for step in directory.steps if step.tool_name == "list_directory"]
    assert directory_steps
    assert json.loads(directory_steps[0].arguments_json)["path"] == "src"


async def test_planner_filters_to_available_catalog_only() -> None:
    planner = HeuristicPlanner()
    only_search = (_tool("search_text", {"query": _STRING}, ("query",)),)
    plan = await planner.plan(
        make_context("Show the history of src/app.ts", locale="en"),
        only_search,
        make_limits(max_plan_steps=2),
    )
    assert [step.tool_name for step in plan.steps] == ["search_text"]


async def test_ranker_orders_exact_symbol_deterministically_and_bounds() -> None:
    ranker = HeuristicEvidenceRanker()
    context = make_context("Where is `parseConfig` defined?", locale="en")
    exact = make_evidence(
        kind=EvidenceKind.SYMBOL,
        method="find_definition",
        symbol_name="parseConfig",
        start_line=10,
        end_line=12,
        excerpt="export function parseConfig() {}",
    )
    file_hit = make_evidence(method="search_text", start_line=40, end_line=42)
    commit = make_evidence(
        kind=EvidenceKind.COMMIT,
        method="get_file_history",
        start_line=1,
        end_line=1,
        excerpt="commit: parse config",
    )
    tree = make_evidence(
        kind=EvidenceKind.TREE_ENTRY,
        path="src/other.ts",
        method="list_directory",
        start_line=1,
        end_line=1,
        excerpt="src/other.ts",
    )
    candidates = RetrievalResult(candidates=(file_hit, commit, exact, tree))
    limits = make_limits(max_evidence=2)
    first = await ranker.rank(context, candidates, limits)
    second = await ranker.rank(context, candidates, limits)
    assert [item.evidence.evidence_id for item in first.evidence] == [
        item.evidence.evidence_id for item in second.evidence
    ]
    assert first.evidence[0].evidence.evidence_id == exact.evidence_id
    assert [item.rank for item in first.evidence] == [1, 2]
    assert first.omitted_count == 2
    assert first.evidence[0].score >= first.evidence[1].score


async def test_ranker_deduplicates_identical_spans() -> None:
    ranker = HeuristicEvidenceRanker()
    first = make_evidence(method="search_text")
    second = make_evidence(method="search_code")
    assert first.evidence_id != second.evidence_id
    candidates = RetrievalResult(candidates=(first, second))
    ranked = await ranker.rank(make_context("parseConfig"), candidates, make_limits())
    assert len(ranked.evidence) == 1
    assert ranked.evidence[0].rank == 1


async def test_grounded_answer_contains_exact_sha_path_and_line() -> None:
    executor = FakeToolExecutor()
    provider = ScriptedModelProvider([grounded_payload])
    orchestrator = build_pipeline(executor, provider)
    outcome = await orchestrator.run(
        make_context("`parseConfig` はどこで定義されていますか"), make_limits()
    )
    response = outcome.response
    text = response.plain_text
    assert response.status is ResponseStatus.OK
    assert SHA in text
    assert "src/app.ts:10-12" in text or "src/app.ts:10" in text
    assert response.citations
    assert all(citation.commit_sha == SHA for citation in response.citations)
    assert all(citation.path == "src/app.ts" for citation in response.citations)
    assert all(citation.citation_id.startswith("ev_") for citation in response.citations)
    assert outcome.diagnostics.model_id == "fake-model"
    assert outcome.diagnostics.iterations >= 1


async def test_model_disabled_degrades_to_extractive_partial() -> None:
    executor = FakeToolExecutor()
    orchestrator = build_pipeline(executor, provider=None)
    outcome = await orchestrator.run(
        make_context("`parseConfig` はどこで定義されていますか"), make_limits()
    )
    response = outcome.response
    assert response.status is ResponseStatus.PARTIAL
    assert "model_disabled" in outcome.diagnostics.degraded_reasons
    assert response.citations
    assert all(citation.citation_id.startswith("ev_") for citation in response.citations)
    assert "src/app.ts:10" in response.plain_text
    assert SHA in response.plain_text


async def test_malformed_model_json_falls_back_without_failing() -> None:
    executor = FakeToolExecutor()
    provider = ScriptedModelProvider(["definitely not valid json {{"])
    outcome = await build_pipeline(executor, provider).run(
        make_context("Show `parseConfig` in src/app.ts", locale="en"), make_limits()
    )
    assert outcome.response.status is ResponseStatus.PARTIAL
    assert "model_invalid_json" in outcome.diagnostics.degraded_reasons
    assert outcome.response.citations
    assert "src/app.ts" in outcome.response.plain_text


async def test_model_error_falls_back_without_leaking_details() -> None:
    executor = FakeToolExecutor()
    provider = ScriptedModelProvider([RuntimeError("provider secret: token=abc123")])
    outcome = await build_pipeline(executor, provider).run(
        make_context("Where is `parseConfig` defined?", locale="en"), make_limits()
    )
    assert outcome.response.status is ResponseStatus.PARTIAL
    assert "model_error" in outcome.diagnostics.degraded_reasons
    assert "token=abc123" not in outcome.response.plain_text
    assert outcome.response.safe_error is None


async def test_empty_evidence_reports_nothing_found_and_tried_methods() -> None:
    executor = FakeToolExecutor(empty=True)
    outcome = await build_pipeline(executor).run(
        make_context("Where is `MissingThing` defined?", locale="en"), make_limits()
    )
    response = outcome.response
    assert response.status is ResponseStatus.PARTIAL
    assert response.citations == ()
    text = response.plain_text
    assert "No matching repository evidence was found." in text
    assert outcome.diagnostics.search_methods
    assert "search_methods" not in text
    assert any(method in text for method in outcome.diagnostics.search_methods)


async def test_empty_vague_query_requests_clarification() -> None:
    executor = FakeToolExecutor(empty=True)
    outcome = await build_pipeline(executor).run(
        make_context("教えてください", locale="ja"), make_limits()
    )
    assert outcome.response.status is ResponseStatus.NEEDS_CLARIFICATION


async def test_tool_call_budget_is_hard_capped() -> None:
    executor = FakeToolExecutor()
    limits = make_limits(max_tool_calls=2, max_plan_steps=4, max_iterations=1)
    outcome = await build_pipeline(executor).run(
        make_context("Show history and callers of `parseConfig` in src/app.ts", locale="en"),
        limits,
    )
    assert len(executor.calls) <= 2
    assert outcome.diagnostics.tool_calls <= 2


async def test_replans_once_on_reasoning_gaps() -> None:
    executor = FakeToolExecutor()
    first = lambda request: grounded_payload(  # noqa: E731
        request,
        gaps=[
            {
                "description": "Need the file change history",
                "suggested_tools": ["get_file_history"],
            }
        ],
    )
    second = grounded_payload
    provider = ScriptedModelProvider([first, second])
    limits = make_limits(max_iterations=3, max_tool_calls=8)
    outcome = await build_pipeline(executor, provider).run(
        make_context("Where is `parseConfig` defined in src/app.ts?", locale="en"), limits
    )
    assert outcome.diagnostics.iterations == 2
    assert len(provider.requests) == 2
    assert any(call.tool_name == "get_file_history" for call in executor.calls)
    assert outcome.response.status is ResponseStatus.OK
    assert outcome.response.follow_ups == ()


async def test_deadline_produces_safe_error_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = FakeToolExecutor(delay=0.5)
    real_timeout = asyncio.timeout
    orchestrator = build_pipeline(executor)
    context = make_context("Where is `parseConfig` defined?", locale="en")
    monkeypatch.setattr(asyncio, "timeout", lambda _seconds: real_timeout(0.05))
    outcome = await orchestrator.run(context, make_limits())
    assert outcome.response.status is ResponseStatus.ERROR
    assert outcome.response.safe_error is not None
    assert "deadline_exceeded" in outcome.diagnostics.degraded_reasons


async def test_unexpected_component_error_is_converted_to_safe_error() -> None:
    class ExplodingRanker(HeuristicEvidenceRanker):
        async def rank(
            self,
            context: QueryContext,
            candidates: RetrievalResult,
            limits: AgentLimits,
        ) -> RankedEvidenceSet:
            raise RuntimeError("internal path C:\\secret\\db.sqlite password=hunter2")

    executor = FakeToolExecutor()
    orchestrator = AgentOrchestrator(
        planner=HeuristicPlanner(),
        retriever=ToolPlanRetriever(executor=executor),
        ranker=ExplodingRanker(),
        reasoner=GroundedReasoner(),
        composer=CitationComposer(),
    )
    outcome = await orchestrator.run(
        make_context("Where is `parseConfig` defined?", locale="en"), make_limits()
    )
    assert outcome.response.status is ResponseStatus.ERROR
    assert outcome.response.safe_error is not None
    assert "hunter2" not in outcome.response.plain_text
    assert "db.sqlite" not in outcome.response.plain_text
    assert "unexpected_error" in outcome.diagnostics.degraded_reasons


async def test_response_respects_channel_length_budget() -> None:
    capabilities = InteractionCapabilities(
        max_response_chars=200,
        supports_markdown=True,
        supports_code_blocks=True,
        supports_typing=False,
        supports_threads=False,
    )
    long_payload = lambda request: grounded_payload(  # noqa: E731
        request,
        fact_text="A very long fact " + ("x" * 500),
        conclusion="A very long conclusion " + ("y" * 500),
        confidence=0.7,
    )
    executor = FakeToolExecutor()
    provider = ScriptedModelProvider([long_payload])
    outcome = await build_pipeline(executor, provider).run(
        make_context(
            "Where is `parseConfig` defined?",
            locale="en",
            capabilities=capabilities,
        ),
        make_limits(),
    )
    response = outcome.response
    assert response.blocks
    assert len(response.plain_text) <= 200
    assert all(len(block.text) <= 10_000 for block in response.blocks)
    assert all(block.kind is not ResponseBlockKind.CODE for block in response.blocks)


async def test_diagnostics_never_include_source_text() -> None:
    executor = FakeToolExecutor()
    outcome = await build_pipeline(executor).run(
        make_context("Where is `parseConfig` defined?", locale="en"), make_limits()
    )
    rendered = repr(outcome.diagnostics)
    assert "export function" not in rendered
    assert "parseConfig" not in rendered
    assert "src/app.ts" not in rendered


async def test_pipeline_only_uses_plan_tools_and_retriever_skips_failures() -> None:
    executor = FakeToolExecutor(failing=("search_code",))
    outcome = await build_pipeline(executor).run(
        make_context("Where is `parseConfig` defined?", locale="en"), make_limits()
    )
    assert outcome.response.status in {ResponseStatus.OK, ResponseStatus.PARTIAL}
    catalog_names = {definition.name for definition in build_catalog()}
    assert all(call.tool_name in catalog_names for call in executor.calls)
    assert any(call.tool_name == "search_code" for call in executor.calls)
    assert outcome.response.citations


async def test_retriever_skips_evidence_outside_authorized_scope() -> None:
    other_repo = RepositoryId("repo_" + "c" * 32)
    foreign = EvidenceCandidate.create(
        kind=EvidenceKind.FILE_EXCERPT,
        repository_id=other_repo,
        commit_sha=SHA,
        path=RepositoryPath(PurePosixPath("src/other.ts")),
        start_line=1,
        end_line=1,
        excerpt="foreign repository content",
        retrieval_method="search_text",
    )
    executor = FakeToolExecutor(evidence=(foreign,))
    context = QueryContext(
        request_id="req_test_1",
        query="Where is `parseConfig` defined?",
        conversation_id=ConversationId.derive("test", "thread-1"),
        capabilities=InteractionCapabilities.discord_thread(),
        locale="en",
        repository_scope=(REPO,),
    )
    outcome = await build_pipeline(executor).run(context, make_limits())
    assert outcome.response.citations == ()
    assert outcome.diagnostics.evidence_ranked == 0


async def test_cancellation_propagates_to_the_caller() -> None:
    executor = FakeToolExecutor(delay=5.0)
    orchestrator = build_pipeline(executor)
    task = asyncio.create_task(
        orchestrator.run(
            make_context("Where is `parseConfig` defined?", locale="en"),
            make_limits(deadline_seconds=30.0),
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_required_model_unavailable_returns_safe_error() -> None:
    executor = FakeToolExecutor()
    orchestrator = build_pipeline(executor, provider=None, fallback_without_model=False)
    outcome = await orchestrator.run(
        make_context("Where is `parseConfig` defined?", locale="en"), make_limits()
    )
    assert outcome.response.status is ResponseStatus.ERROR
    assert outcome.response.safe_error is not None
    assert "model_required_but_unavailable" in outcome.diagnostics.degraded_reasons
    assert outcome.response.citations == ()
