"""Safe Git runner security and resource-bound tests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from conftest import GitRepositoryFixture

from codeatlas.adapters.repository import git_runner
from codeatlas.adapters.repository.git_runner import GitSubcommand, SafeGitRunner
from codeatlas.domain.errors import (
    LimitExceededError,
    RepositoryError,
    RequestTimeoutError,
    SecurityError,
)


def test_executable_is_fixed() -> None:
    """Arbitrary executable paths cannot be configured as Git."""
    with pytest.raises(ValueError):
        SafeGitRunner(executable="cmd.exe")


@pytest.mark.asyncio
async def test_runtime_command_allowlist_rejects_strings(
    git_repository: GitRepositoryFixture,
) -> None:
    """Runtime callers cannot bypass the typed subcommand allowlist."""
    runner = SafeGitRunner()
    with pytest.raises(SecurityError):
        await runner.run(git_repository.root, "status")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_argument_injection_is_data_not_shell(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    """Shell metacharacters remain one argv value and cannot create files."""
    marker = tmp_path / "marker"
    runner = SafeGitRunner()
    with pytest.raises(RepositoryError) as captured:
        await runner.run(
            git_repository.root,
            GitSubcommand.REV_PARSE,
            "--verify",
            f"HEAD;echo injected>{marker}",
        )
    assert marker.exists() is False
    assert str(marker) not in str(captured.value)


@pytest.mark.asyncio
async def test_stdout_limit_terminates_large_output(
    git_repository: GitRepositoryFixture,
) -> None:
    """Output is bounded while streams are read, rather than after buffering everything."""
    runner = SafeGitRunner(output_limit_bytes=128)
    with pytest.raises(LimitExceededError):
        await runner.run(
            git_repository.root,
            GitSubcommand.SHOW,
            f"{git_repository.head_sha}:large.txt",
        )


@pytest.mark.asyncio
async def test_timeout_is_safely_classified(git_repository: GitRepositoryFixture) -> None:
    """An expired command deadline becomes a safe timeout error."""
    runner = SafeGitRunner(timeout_seconds=1e-9)
    with pytest.raises(RequestTimeoutError):
        await runner.run(git_repository.root, GitSubcommand.REV_PARSE, "HEAD")


@pytest.mark.asyncio
async def test_cancellation_terminates_reader_tasks(
    git_repository: GitRepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation propagates after process and stream cleanup."""
    original_reader = git_runner._read_limited

    async def wait_forever(
        stream: asyncio.StreamReader,
        limit: int,
    ) -> bytes:
        del stream, limit
        await asyncio.Event().wait()
        return b""

    monkeypatch.setattr(git_runner, "_read_limited", wait_forever)
    runner = SafeGitRunner(timeout_seconds=30)
    task = asyncio.create_task(runner.run(git_repository.root, GitSubcommand.REV_PARSE, "HEAD"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(git_runner, "_read_limited", original_reader)


@pytest.mark.asyncio
async def test_fixed_git_controls_are_passed(
    git_repository: GitRepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pager, config hooks, attributes, external diff, and stdin are controlled."""
    captured_arguments: tuple[object, ...] = ()
    captured_keywords: dict[str, object] = {}
    original = asyncio.create_subprocess_exec

    async def capture(*arguments: object, **keywords: object) -> asyncio.subprocess.Process:
        nonlocal captured_arguments, captured_keywords
        captured_arguments = arguments
        captured_keywords = keywords
        return await original(*arguments, **keywords)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    await SafeGitRunner().run(git_repository.root, GitSubcommand.REV_PARSE, "HEAD")
    assert captured_arguments[:4] == (
        "git",
        "--no-pager",
        "--no-optional-locks",
        "--literal-pathspecs",
    )
    assert "diff.external=" in captured_arguments
    assert f"core.attributesFile={os.devnull}" in captured_arguments
    assert f"core.hooksPath={os.devnull}" in captured_arguments
    assert "core.fsmonitor=false" in captured_arguments
    assert captured_keywords["stdin"] is asyncio.subprocess.DEVNULL
    environment = captured_keywords["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_EXTERNAL_DIFF"] == ""
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
