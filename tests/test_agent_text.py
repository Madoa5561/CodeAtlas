"""Deterministic query analysis and planner behaviour for locale-specific terms."""

# ruff: noqa: RUF001

from __future__ import annotations

import json

import pytest

from codeatlas.application.agent import HeuristicPlanner
from codeatlas.application.agent._text import analyze, expand_domain_terms
from codeatlas.application.tools.catalog import build_tool_registry
from codeatlas.domain.agent_models import AgentLimits, QueryContext
from codeatlas.domain.conversation_models import ConversationId
from codeatlas.domain.interaction_models import InteractionCapabilities


def make_context(query: str, locale: str = "ja") -> QueryContext:
    """Build a minimal query context for planner tests."""
    return QueryContext(
        request_id="req_test_1",
        query=query,
        conversation_id=ConversationId.derive("cli", "test"),
        capabilities=InteractionCapabilities.plain_cli(),
        locale=locale,
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("認証はどのファイルで処理されていますか？", ("auth", "login", "file")),
        ("ログイン処理の流れを教えて", ("auth", "login")),
        ("How does authentication work?", ("auth", "login")),
        ("設定はどこですか？", ("config", "settings")),
        ("依存関係を確認したい", ("import", "dependency")),
    ],
)
def test_domain_terms_expand_to_source_identifiers(query: str, expected: tuple[str, ...]) -> None:
    """Japanese and English domain terms map to deterministic identifiers."""
    assert expand_domain_terms(query) == expected


def test_japanese_question_without_identifier_still_has_focus() -> None:
    """A Japanese domain question becomes searchable instead of falling back to a literal phrase."""
    signals = analyze("認証はどのファイルで処理されていますか？")

    assert signals.identifiers[:2] == ("auth", "login")
    assert signals.has_focus is True
    assert "auth" in signals.terms


@pytest.mark.asyncio
async def test_planner_uses_domain_identifier_for_japanese_question() -> None:
    """The heuristic planner searches source identifiers instead of the raw Japanese sentence."""
    catalog = build_tool_registry().definitions()
    plan = await HeuristicPlanner().plan(
        make_context("認証はどこで実装されていますか？"), catalog, AgentLimits()
    )

    assert plan.steps
    assert plan.steps[0].tool_name in {"find_definition", "search_code"}
    assert '"auth"' in plan.steps[0].arguments_json
    assert all("認証" not in step.arguments_json for step in plan.steps)


@pytest.mark.asyncio
async def test_planner_export_intent_uses_find_exports() -> None:
    """Export questions schedule the dedicated export tool."""
    catalog = build_tool_registry().definitions()
    plan = await HeuristicPlanner().plan(
        make_context("Client は外部に export されていますか？"), catalog, AgentLimits()
    )

    assert "find_exports" in [step.tool_name for step in plan.steps]
    export_step = next(step for step in plan.steps if step.tool_name == "find_exports")
    assert json.loads(export_step.arguments_json)["name"] == "Client"


@pytest.mark.asyncio
async def test_planner_graph_intent_uses_traverse_graph() -> None:
    """Call-graph questions schedule the bounded graph traversal tool."""
    catalog = build_tool_registry().definitions()
    plan = await HeuristicPlanner().plan(
        make_context("Service の呼び出しグラフを見たい"), catalog, AgentLimits()
    )

    assert "traverse_graph" in [step.tool_name for step in plan.steps]
    graph_step = next(step for step in plan.steps if step.tool_name == "traverse_graph")
    arguments = json.loads(graph_step.arguments_json)
    assert arguments["start"] == "Service"
    assert arguments["start_type"] == "symbol"


@pytest.mark.asyncio
async def test_planner_pull_request_intent_uses_github_tools() -> None:
    """A PR review question schedules metadata and diff reads with its number."""
    catalog = build_tool_registry().definitions()
    plan = await HeuristicPlanner().plan(
        make_context("PR #12 をレビューして"), catalog, AgentLimits()
    )

    tools = [step.tool_name for step in plan.steps]
    assert "get_pull_request" in tools
    assert "get_pull_request_diff" in tools
    pr_step = next(step for step in plan.steps if step.tool_name == "get_pull_request")
    assert json.loads(pr_step.arguments_json)["number"] == "12"
