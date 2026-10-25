"""Composition-root and diagnostic CLI integration tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Protocol

import httpx
import pytest

from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.domain.errors import IndexSchemaError
from codeatlas.entrypoints import cli
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.runtime import CodeAtlasRuntime, build_agent_limits


class GitFixture(Protocol):
    """The subset of the shared Git repository fixture used by these tests."""

    root: Path


def make_settings(tmp_path: Path, **overrides: object) -> CodeAtlasSettings:
    """Build production-like settings pointing at an isolated SQLite database."""
    url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    values: dict[str, object] = {
        "database": {"url": url},
        "ollama": {"enabled": False},
        "discord": {"enabled": False},
        "repository": {"registered_paths": ()},
    }
    values.update(overrides)
    return CodeAtlasSettings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_runtime_lifecycle_requires_current_schema(tmp_path: Path) -> None:
    """The runtime refuses to start before explicit migration."""
    settings = make_settings(tmp_path)
    runtime = CodeAtlasRuntime.create(settings, enable_discord=False)
    with pytest.raises(IndexSchemaError):
        await runtime.start()
    assert runtime.started is False


@pytest.mark.asyncio
async def test_runtime_starts_with_current_schema_and_registered_repository(
    tmp_path: Path, git_repository: GitFixture
) -> None:
    """A migrated database and registered repository produce a usable runtime."""
    root = git_repository.root
    settings = make_settings(tmp_path, repository={"registered_paths": (root,)})
    upgrade_database(settings.database.url)
    runtime = CodeAtlasRuntime.create(settings, enable_discord=False)
    try:
        await runtime.start()
        assert runtime.started is True
        assert len(runtime.registered_repositories) == 1
        assert runtime.registered_repositories[0].name == "repository"
        assert len(runtime.tool_executor.catalog()) >= 20
        assert build_agent_limits(settings).max_evidence >= 1
    finally:
        await runtime.stop()
    assert runtime.started is False


def test_runtime_selects_enabled_deepseek_provider(tmp_path: Path) -> None:
    """DeepSeek takes precedence when enabled and Ollama is disabled."""
    settings = make_settings(
        tmp_path,
        deepseek={"enabled": True, "api_key": "sk-test"},
        ollama={"enabled": False},
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    runtime = CodeAtlasRuntime.create(
        settings,
        enable_discord=False,
        model_transport=httpx.MockTransport(handler),
    )
    assert runtime.model_plugin.provider.provider_id == "deepseek"
    assert runtime.model_plugin.provider.model_id == "deepseek-flash"


def test_cli_repos_list_json(
    tmp_path: Path,
    git_repository: GitFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The diagnostic CLI lists registered repositories without needing a model."""
    root = git_repository.root
    settings = make_settings(tmp_path, repository={"registered_paths": (root,)})
    upgrade_database(settings.database.url)
    monkeypatch.setenv("CODEATLAS_DATABASE__URL", settings.database.url)
    monkeypatch.setenv(
        "CODEATLAS_REPOSITORY__REGISTERED_PATHS",
        json.dumps([str(root)]),
    )
    monkeypatch.setenv("CODEATLAS_OLLAMA__ENABLED", "false")
    monkeypatch.setenv("CODEATLAS_DISCORD__ENABLED", "false")
    monkeypatch.setattr(sys, "argv", ["codeatlas-ask", "--json", "repos", "list"])

    with pytest.raises(SystemExit) as captured:
        cli.main()

    assert captured.value.code == 0
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert len(payload) == 1
    assert payload[0]["name"] == "repository"
    assert payload[0]["repository_id"].startswith("repo_")
