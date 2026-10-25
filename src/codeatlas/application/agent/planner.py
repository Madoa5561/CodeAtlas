"""Deterministic, locale-aware investigation planning for the agent pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from codeatlas.application.agent._text import QuerySignals, analyze, localized
from codeatlas.domain.agent_models import (
    AgentLimits,
    InvestigationPlan,
    InvestigationStep,
    QueryContext,
    RetrievalGap,
    ToolDefinition,
)
from codeatlas.domain.errors import AgentError

_MAX_PURPOSE_CHARS = 500
_MAX_OBJECTIVE_CHARS = 1_000


@dataclass(frozen=True, slots=True)
class _Draft:
    """One planned tool invocation before schema-aware argument encoding."""

    tool: ToolDefinition
    purpose_ja: str
    purpose_en: str
    query: str | None = None
    name: str | None = None
    path: str | None = None
    directory: str | None = None
    revision: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _schema_properties(definition: ToolDefinition) -> dict[str, dict[str, Any]]:
    try:
        parsed: Any = json.loads(definition.input_schema_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    properties = parsed.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {str(key): value for key, value in properties.items() if isinstance(value, dict)}


def _required_properties(definition: ToolDefinition) -> tuple[str, ...]:
    try:
        parsed: Any = json.loads(definition.input_schema_json)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, dict):
        return ()
    required = parsed.get("required")
    if not isinstance(required, list):
        return ()
    return tuple(str(item) for item in required if isinstance(item, str))


def _property_value(property_name: str, draft: _Draft) -> str | None:
    name = property_name.casefold()
    if "qualified_name" in name:
        if draft.name is not None and "." in draft.name:
            return draft.name
        return None
    if "query" in name or "pattern" in name or "text" in name or "term" in name:
        return draft.query
    if "path_prefix" in name:
        return draft.directory
    if "path" in name or "file" in name or "directory" in name or "dir" in name:
        return draft.path or draft.directory
    if "revision" in name or name.endswith("ref") or name.endswith("sha") or "commit" in name:
        return draft.revision
    if name in {"name", "symbol", "target", "symbol_name"}:
        if name == "symbol" and draft.tool.name == "get_dependencies":
            # get_dependencies accepts exactly one of symbol or path; never derive
            # the symbol from the question text or both arguments would be sent.
            return draft.name
        return draft.name or draft.query
    return draft.extra.get(property_name)


def build_arguments(draft: _Draft) -> str | None:
    """Encode one draft as tool JSON, or return None when required input is missing."""
    properties = _schema_properties(draft.tool)
    if not properties:
        fallback_arguments = {key: value for key, value in draft.extra.items() if value is not None}
        return json.dumps(fallback_arguments, ensure_ascii=False, sort_keys=True)
    arguments: dict[str, str] = {}
    for property_name in properties:
        value = _property_value(property_name, draft)
        if value is not None and value.strip():
            arguments[property_name] = value
    for required_name in _required_properties(draft.tool):
        if required_name in arguments:
            continue
        if required_name in draft.extra and draft.extra[required_name].strip():
            arguments[required_name] = draft.extra[required_name]
            continue
        return None
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


class HeuristicPlanner:
    """Deterministic planner that never needs a language model.

    It extracts identifiers, quoted phrases, paths, and specialized intents, then
    maps them onto tools that actually exist in the injected catalog.
    """

    def __init__(self, *, max_steps: int | None = None) -> None:
        if max_steps is not None and not 1 <= max_steps <= 20:
            raise ValueError("invalid planner step limit")
        self._max_steps = max_steps

    async def plan(
        self,
        context: QueryContext,
        catalog: tuple[ToolDefinition, ...],
        limits: AgentLimits,
    ) -> InvestigationPlan:
        """Plan the first bounded retrieval round."""
        by_name = {definition.name: definition for definition in catalog}
        if not by_name:
            raise AgentError("No retrieval tools are available.")
        signals = analyze(context.query)
        step_limit = self._step_limit(limits)
        drafts = self._first_round_drafts(signals, by_name)
        steps = self._encode(drafts, signals, context.locale, step_limit)
        if not steps:
            steps = self._fallback_steps(signals, by_name, context.locale, step_limit)
        return InvestigationPlan(
            objective=self._objective(context.query),
            steps=steps,
            search_methods=_methods(steps),
            rationale=localized(
                context.locale,
                ja="決定的なキーワードと識別子の抽出に基づく計画です。",
                en="Deterministic plan derived from extracted keywords and identifiers.",
            ),
        )

    async def replan(
        self,
        context: QueryContext,
        previous: InvestigationPlan,
        gaps: tuple[RetrievalGap, ...],
        catalog: tuple[ToolDefinition, ...],
        limits: AgentLimits,
    ) -> InvestigationPlan:
        """Plan one bounded follow-up round for disclosed retrieval gaps."""
        by_name = {definition.name: definition for definition in catalog}
        if not by_name:
            raise AgentError("No retrieval tools are available.")
        signals = analyze(context.query)
        step_limit = min(self._step_limit(limits), 4)
        drafts: list[_Draft] = []
        for gap in gaps:
            for tool_name in gap.suggested_tools:
                definition = by_name.get(tool_name)
                if definition is not None:
                    drafts.append(self._draft_for_tool(definition, signals))
        if not drafts:
            drafts = self._first_round_drafts(signals, by_name)
        executed = {(step.tool_name, step.arguments_json) for step in previous.steps}
        fresh: list[_Draft] = []
        seen: set[tuple[str, str]] = set()
        for draft in drafts:
            arguments_json = build_arguments(draft)
            if arguments_json is None:
                continue
            key = (draft.tool.name, arguments_json)
            if key in executed or key in seen:
                continue
            seen.add(key)
            fresh.append(draft)
            if len(fresh) >= step_limit:
                break
        steps = self._encode(fresh, signals, context.locale, step_limit)
        if not steps:
            steps = previous.steps
        objective = localized(
            context.locale,
            ja="追加調査: " + " / ".join(gap.description for gap in gaps),
            en="Follow-up investigation: " + " / ".join(gap.description for gap in gaps),
        )
        return InvestigationPlan(
            objective=objective[:_MAX_OBJECTIVE_CHARS],
            steps=steps,
            search_methods=_methods(steps),
            rationale=localized(
                context.locale,
                ja="検索で不足した情報を補うための追加計画です。",
                en="Follow-up plan covering information missing from the first round.",
            ),
        )

    def _step_limit(self, limits: AgentLimits) -> int:
        limit = limits.max_plan_steps
        if self._max_steps is not None:
            limit = min(limit, self._max_steps)
        return limit

    def _encode(
        self,
        drafts: list[_Draft],
        signals: QuerySignals,
        locale: str,
        step_limit: int,
    ) -> tuple[InvestigationStep, ...]:
        steps: list[InvestigationStep] = []
        seen: set[tuple[str, str]] = set()
        for draft in drafts:
            arguments_json = build_arguments(draft)
            if arguments_json is None:
                continue
            key = (draft.tool.name, arguments_json)
            if key in seen:
                continue
            seen.add(key)
            purpose = localized(locale, ja=draft.purpose_ja, en=draft.purpose_en)
            steps.append(
                InvestigationStep(
                    step_id=f"step_{len(steps) + 1:02d}",
                    tool_name=draft.tool.name,
                    arguments_json=arguments_json,
                    purpose=purpose[:_MAX_PURPOSE_CHARS],
                )
            )
            if len(steps) >= step_limit:
                break
        return tuple(steps)

    def _first_round_drafts(
        self,
        signals: QuerySignals,
        by_name: dict[str, ToolDefinition],
    ) -> list[_Draft]:
        drafts: list[_Draft] = []

        def add(tool_names: tuple[str, ...], *, extra: dict[str, str] | None = None) -> None:
            for tool_name in tool_names:
                definition = by_name.get(tool_name)
                if definition is None:
                    continue
                draft = self._draft_for_tool(definition, signals)
                if extra:
                    draft = _Draft(
                        tool=draft.tool,
                        purpose_ja=draft.purpose_ja,
                        purpose_en=draft.purpose_en,
                        query=draft.query,
                        name=draft.name,
                        path=draft.path,
                        directory=draft.directory,
                        revision=draft.revision,
                        extra={**draft.extra, **extra},
                    )
                drafts.append(draft)
                return

        if signals.wants_repository_list:
            add(("list_repositories",))
        if signals.identifiers or signals.quoted:
            if signals.wants_definition:
                add(("find_definition", "find_symbol"))
        if "search_lexical" in by_name:
            add(("search_lexical",))
        if "search_vector" in by_name:
            add(("search_vector",))
        search_tools = (
            ("search_code", "search_text")
            if signals.identifiers
            else ("search_text", "search_code")
        )
        add(search_tools)
        if signals.identifiers or signals.quoted:
            if signals.wants_definition:
                add(("find_definition", "find_symbol"))
            else:
                add(("find_symbol", "find_definition"))
        if signals.wants_exports:
            add(("find_exports", "find_symbol"))
        if signals.wants_graph:
            add(("traverse_graph",))
        if signals.wants_pull_request and signals.github_number is not None:
            add(("get_pull_request",))
            add(("get_pull_request_diff",))
        if signals.wants_issue:
            add(("list_issues",))
        if signals.urls:
            add(("fetch_http",))
        if signals.wants_callers:
            add(("find_callers", "find_references"))
        if signals.wants_references:
            add(("find_references", "find_symbol"))
        if signals.wants_callees:
            add(("find_callees", "find_symbol"))
        if signals.wants_dependencies:
            add(("get_dependencies", "find_symbol"))
        if signals.wants_dependents:
            add(("get_dependents", "find_symbol"))
        if signals.files:
            add(("read_file",))
            if signals.wants_blame:
                add(("get_blame",))
            if signals.wants_history:
                add(("get_file_history", "get_blame"))
        if signals.wants_diff:
            add(("get_diff",))
        if signals.wants_history and not signals.files:
            if signals.commit_sha is not None:
                add(("get_commit", "search_commits"))
            add(("search_commits", "get_commit"))
        if signals.globs or signals.directories or signals.wants_directory:
            add(("list_directory", "read_file"))
        return drafts

    def _draft_for_tool(self, definition: ToolDefinition, signals: QuerySignals) -> _Draft:
        """Build a semantic draft for one catalog tool, preferring its real schema."""
        tool = definition.name
        phrase = signals.search_phrase
        primary_identifier = signals.identifiers[0] if signals.identifiers else None
        name = primary_identifier or phrase
        file_path = signals.files[0] if signals.files else None
        directory = signals.directories[0] if signals.directories else None
        if directory is not None:
            directory = directory.rstrip("/") or "."
        if tool in {"search_text", "search_code"}:
            return _Draft(
                tool=definition,
                purpose_ja="質問のキーワードでソース本文を検索します。",
                purpose_en="Search source text for the question keywords.",
                query=phrase,
                directory=directory,
                path=file_path,
            )
        if tool in {"find_symbol", "find_definition", "find_references", "find_callers"}:
            return _Draft(
                tool=definition,
                purpose_ja="シンボル宣言と参照箇所を特定します。",
                purpose_en="Locate the symbol declaration and related locations.",
                name=name,
                query=phrase,
            )
        if tool == "find_exports":
            return _Draft(
                tool=definition,
                purpose_ja="シンボルのexportと公開APIを確認します。",
                purpose_en="Verify the symbol export and public API surface.",
                name=name,
                path=file_path,
                query=phrase,
            )
        if tool == "traverse_graph":
            start = name or file_path or phrase
            return _Draft(
                tool=definition,
                purpose_ja="呼び出し・依存グラフをたどります。",
                purpose_en="Traverse the call and dependency graph.",
                name=name,
                path=file_path,
                query=phrase,
                extra={
                    "start": start,
                    "start_type": (
                        "file" if not signals.identifiers and file_path is not None else "symbol"
                    ),
                },
            )
        if tool in {"get_pull_request", "get_pull_request_diff"}:
            return _Draft(
                tool=definition,
                purpose_ja="GitHubのpull requestを確認します。",
                purpose_en="Read the referenced GitHub pull request.",
                query=phrase,
                extra={"number": str(signals.github_number or "")},
            )
        if tool == "list_issues":
            return _Draft(
                tool=definition,
                purpose_ja="GitHubのissue一覧を確認します。",
                purpose_en="List bounded GitHub issues.",
                query=phrase,
            )
        if tool == "fetch_http":
            return _Draft(
                tool=definition,
                purpose_ja="許可された外部URLから本文を取得します。",
                purpose_en="Fetch an allowlisted external document.",
                query=phrase,
                extra={"url": signals.urls[0] if signals.urls else ""},
            )
        if tool == "find_callees":
            return _Draft(
                tool=definition,
                purpose_ja="対象シンボルが呼び出す関数を確認します。",
                purpose_en="Inspect functions called by the target symbol.",
                name=name,
                query=phrase,
            )
        if tool in {"get_dependencies", "get_dependents"}:
            if tool == "get_dependencies":
                target_path = file_path if file_path is not None else directory
                return _Draft(
                    tool=definition,
                    purpose_ja="対象の依存関係を確認します。",
                    purpose_en="Inspect dependencies of the target.",
                    name=primary_identifier,
                    path=None if primary_identifier is not None else target_path,
                    query=phrase,
                )
            return _Draft(
                tool=definition,
                purpose_ja="対象を参照する側の依存関係を確認します。",
                purpose_en="Inspect dependents of the target.",
                name=name,
                query=phrase,
            )
        if tool == "read_file":
            return _Draft(
                tool=definition,
                purpose_ja="対象ファイルの内容を読み取ります。",
                purpose_en="Read the referenced file.",
                path=file_path,
                query=phrase,
            )
        if tool in {"list_directory", "list_branches", "list_tags"}:
            return _Draft(
                tool=definition,
                purpose_ja="リポジトリの構成を確認します。",
                purpose_en="Inspect repository structure.",
                directory=directory or ".",
                path=directory,
                query=phrase,
            )
        if tool in {"get_file_history", "get_blame"}:
            return _Draft(
                tool=definition,
                purpose_ja="対象ファイルの変更履歴を確認します。",
                purpose_en="Inspect the file change history.",
                path=file_path,
                query=phrase,
            )
        if tool in {"search_commits", "get_commit", "get_diff", "compare_refs"}:
            return _Draft(
                tool=definition,
                purpose_ja="Git履歴と変更内容を確認します。",
                purpose_en="Inspect git history and changes.",
                query=phrase,
                path=file_path,
                revision=signals.commit_sha,
            )
        if tool in {"list_repositories", "get_repository"}:
            return _Draft(
                tool=definition,
                purpose_ja="登録済みリポジトリを確認します。",
                purpose_en="Inspect registered repositories.",
            )
        return _Draft(
            tool=definition,
            purpose_ja="追加の検索を実行します。",
            purpose_en="Run an additional retrieval lookup.",
            query=phrase,
            name=name,
            path=file_path,
            directory=directory,
            revision=signals.commit_sha,
        )

    def _fallback_steps(
        self,
        signals: QuerySignals,
        by_name: dict[str, ToolDefinition],
        locale: str,
        step_limit: int,
    ) -> tuple[InvestigationStep, ...]:
        fallback = [self._draft_for_tool(definition, signals) for definition in by_name.values()]
        steps = self._encode(fallback, signals, locale, step_limit)
        if steps:
            return steps
        first = next(iter(by_name.values()))
        return (
            InvestigationStep(
                step_id="step_01",
                tool_name=first.name,
                arguments_json="{}",
                purpose=localized(
                    locale,
                    ja="利用可能な検索ツールで調査します。",
                    en="Investigate with the available retrieval tool.",
                ),
            ),
        )

    @staticmethod
    def _objective(query: str) -> str:
        collapsed = " ".join(query.split())
        return collapsed[:_MAX_OBJECTIVE_CHARS] or "investigate the question"


def _methods(steps: tuple[InvestigationStep, ...]) -> tuple[str, ...]:
    methods: list[str] = []
    for step in steps:
        if step.tool_name not in methods:
            methods.append(step.tool_name)
    return tuple(methods)
