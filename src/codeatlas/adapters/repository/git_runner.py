"""Bounded asynchronous runner for an allowlist of read-only Git commands."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codeatlas.domain.errors import (
    LimitExceededError,
    RepositoryError,
    RequestTimeoutError,
    SecurityError,
)


class GitSubcommand(StrEnum):
    """Read-only Git subcommands available to the repository adapter."""

    REV_PARSE = "rev-parse"
    SHOW = "show"
    LS_TREE = "ls-tree"
    FOR_EACH_REF = "for-each-ref"
    LOG = "log"
    BLAME = "blame"
    DIFF = "diff"
    CAT_FILE = "cat-file"
    CHECK_ATTR = "check-attr"


@dataclass(frozen=True, slots=True)
class GitResult:
    """Bounded raw output from a successful Git process."""

    stdout: bytes
    stderr: bytes


class _StreamLimitReached(Exception):
    pass


async def _read_limited(
    stream: asyncio.StreamReader,
    limit: int,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise _StreamLimitReached
        chunks.append(chunk)
    return b"".join(chunks)


class SafeGitRunner:
    """Execute only fixed Git subcommands with a minimal deterministic environment."""

    def __init__(
        self,
        *,
        executable: str = "git",
        timeout_seconds: float = 30.0,
        output_limit_bytes: int = 4 * 1024**2,
    ) -> None:
        if executable not in {"git", "git.exe"}:
            raise ValueError("Git executable must be git or git.exe")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes

    async def run(
        self,
        repository_root: Path,
        subcommand: GitSubcommand,
        *arguments: str,
        output_limit_bytes: int | None = None,
    ) -> GitResult:
        """Run one allowlisted Git operation without a shell or writable stdin."""
        if not isinstance(subcommand, GitSubcommand):
            raise SecurityError("The Git operation is not allowed.")
        limit = output_limit_bytes or self.output_limit_bytes
        argv = (
            self.executable,
            "--no-pager",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "color.ui=false",
            "-c",
            "core.pager=cat",
            "-c",
            "diff.external=",
            subcommand.value,
            *arguments,
        )
        environment = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_EXTERNAL_DIFF": "",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", ""),
        }
        for name in ("SYSTEMROOT", "COMSPEC", "WINDIR"):
            if value := os.environ.get(name):
                environment[name] = value

        process: asyncio.subprocess.Process | None = None
        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=repository_root,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.stdout is None or process.stderr is None:
                raise RepositoryError("Git process streams were unavailable.")
            stdout_task = asyncio.create_task(_read_limited(process.stdout, limit))
            stderr_task = asyncio.create_task(_read_limited(process.stderr, limit))
            async with asyncio.timeout(self.timeout_seconds):
                stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
                return_code = await process.wait()
        except TimeoutError as error:
            await self._terminate(process, stdout_task, stderr_task)
            raise RequestTimeoutError(internal_cause=error) from error
        except _StreamLimitReached as error:
            await self._terminate(process, stdout_task, stderr_task)
            raise LimitExceededError(internal_cause=error) from error
        except asyncio.CancelledError:
            await self._terminate(process, stdout_task, stderr_task)
            raise
        except OSError as error:
            await self._terminate(process, stdout_task, stderr_task)
            raise RepositoryError(internal_cause=error) from error

        if return_code != 0:
            diagnostic = RuntimeError(stderr.decode("utf-8", errors="replace")[:4096])
            raise RepositoryError(internal_cause=diagnostic)
        return GitResult(stdout=stdout, stderr=stderr)

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process | None,
        stdout_task: asyncio.Task[bytes] | None,
        stderr_task: asyncio.Task[bytes] | None,
    ) -> None:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        for task in (stdout_task, stderr_task):
            if task is not None and not task.done():
                task.cancel()
        pending = [task for task in (stdout_task, stderr_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
