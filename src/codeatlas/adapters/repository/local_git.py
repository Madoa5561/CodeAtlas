"""Read-only local repository provider backed by a bounded Git CLI runner."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from codeatlas.adapters.repository.content_security import (
    content_digest,
    reject_excluded_path,
    validate_text_bytes,
)
from codeatlas.adapters.repository.git_runner import GitSubcommand, SafeGitRunner
from codeatlas.adapters.repository.path_security import canonical_path_key
from codeatlas.domain.errors import (
    BinaryFileError,
    InvalidRevisionError,
    LimitExceededError,
    RepositoryError,
    RequestTimeoutError,
    SecretDetectedError,
    SecurityError,
)
from codeatlas.domain.repository_models import (
    BlameEntry,
    CommitInfo,
    DiffResult,
    FileContent,
    FileHistoryEntry,
    RepositoryDescriptor,
    RepositoryEntry,
    RepositoryEntryType,
    RepositoryId,
    RepositoryLocation,
    RepositoryPath,
    RepositoryPolicy,
    ResolvedRevision,
    RevisionRef,
    TextSearchHit,
    TextSearchQuery,
)
from codeatlas.domain.repository_ports import RepositoryRegistryPort

_COMMIT_FORMAT = "%H%x00%P%x00%an%x00%ae%x00%aI%x00%cI%x00%s%x00%b%x00"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _decode_git_text(raw: bytes, safe_message: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryError(safe_message, internal_cause=error) from error


class LocalGitRepositoryProvider:
    """Provide SHA-pinned repository reads without consulting working-tree content."""

    def __init__(
        self,
        registry: RepositoryRegistryPort,
        runner: SafeGitRunner,
        policy: RepositoryPolicy,
        *,
        ripgrep_executable: str = "rg",
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._policy = policy
        self._ripgrep_executable = ripgrep_executable

    async def register_local_repository(self, location: RepositoryLocation) -> RepositoryDescriptor:
        """Validate and register one canonical non-bare, non-submodule Git repository."""
        try:
            requested = location.path.expanduser().resolve(strict=True)
        except OSError as error:
            raise RepositoryError(
                "The local repository is unavailable.", internal_cause=error
            ) from error
        if not requested.is_dir():
            raise RepositoryError("The local repository is unavailable.")
        try:
            top = await self._git_text(requested, GitSubcommand.REV_PARSE, "--show-toplevel")
            root = Path(top.strip()).resolve(strict=True)
            bare = await self._git_text(root, GitSubcommand.REV_PARSE, "--is-bare-repository")
            if bare.strip() == "true":
                raise RepositoryError("Bare repositories are not supported.")
            superproject = await self._git_text(
                root, GitSubcommand.REV_PARSE, "--show-superproject-working-tree"
            )
            if superproject.strip():
                raise RepositoryError("Git submodules are not supported in M2.")
            common_dir_text = await self._git_text(
                root, GitSubcommand.REV_PARSE, "--git-common-dir"
            )
        except RepositoryError:
            raise
        self._enforce_repository_size(root)
        common_dir = Path(common_dir_text.strip())
        if not common_dir.is_absolute():
            common_dir = root / common_dir
        common_dir = common_dir.resolve(strict=True)
        key = f"{canonical_path_key(root)}\x00{canonical_path_key(common_dir)}"
        common_stat = common_dir.stat()
        filesystem_identity = f"{common_stat.st_dev}:{common_stat.st_ino}"
        if common_stat.st_ino == 0:
            filesystem_identity = canonical_path_key(common_dir)
        identifier = RepositoryId(
            f"repo_{hashlib.sha256(filesystem_identity.encode()).hexdigest()[:32]}"
        )
        default_branch: str | None
        try:
            default_branch = (
                await self._git_text(root, GitSubcommand.REV_PARSE, "--abbrev-ref", "HEAD")
            ).strip()
        except RepositoryError:
            default_branch = None
        descriptor = RepositoryDescriptor(
            repository_id=identifier,
            canonical_location=RepositoryLocation(root),
            name=root.name,
            default_branch=default_branch,
            registered_at=datetime.now(UTC),
        )
        return self._registry.add(descriptor, key)

    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        """Return a registered repository descriptor."""
        return self._registry.get(repository_id)

    async def resolve_revision(
        self, repository_id: RepositoryId, revision: RevisionRef
    ) -> ResolvedRevision:
        """Resolve a branch, tag, or SHA to one unambiguous full commit SHA."""
        descriptor = self._registry.get(repository_id)
        try:
            if _FULL_SHA.fullmatch(revision.value) is None:
                refs = await self._runner.run(
                    descriptor.canonical_location.path,
                    GitSubcommand.FOR_EACH_REF,
                    "--format=%(refname)%00",
                    f"refs/heads/{revision.value}",
                    f"refs/tags/{revision.value}",
                )
                exact_names = {
                    name
                    for item in refs.stdout.split(b"\x00")
                    if (
                        name := _decode_git_text(
                            item, "Git reference metadata was malformed."
                        ).strip()
                    )
                    in {f"refs/heads/{revision.value}", f"refs/tags/{revision.value}"}
                }
                if len(exact_names) > 1:
                    raise InvalidRevisionError()
            output = await self._git_text(
                descriptor.canonical_location.path,
                GitSubcommand.REV_PARSE,
                "--verify",
                "--end-of-options",
                f"{revision.value}^{{commit}}",
            )
        except InvalidRevisionError:
            raise
        except RepositoryError as error:
            raise InvalidRevisionError(internal_cause=error) from error
        commit_sha = output.strip()
        if _FULL_SHA.fullmatch(commit_sha) is None:
            raise InvalidRevisionError()
        return ResolvedRevision(repository_id, commit_sha, revision)

    async def list_branches(self, repository_id: RepositoryId) -> tuple[ResolvedRevision, ...]:
        """List branches in stable name order with every value resolved to a commit."""
        return await self._list_refs(repository_id, "refs/heads")

    async def list_tags(self, repository_id: RepositoryId) -> tuple[ResolvedRevision, ...]:
        """List tags in stable name order with annotated tags peeled to commits."""
        return await self._list_refs(repository_id, "refs/tags")

    async def _list_refs(
        self, repository_id: RepositoryId, namespace: str
    ) -> tuple[ResolvedRevision, ...]:
        descriptor = self._registry.get(repository_id)
        result = await self._runner.run(
            descriptor.canonical_location.path,
            GitSubcommand.FOR_EACH_REF,
            "--sort=refname",
            "--format=%(refname:short)%00",
            namespace,
        )
        names = [
            name
            for item in result.stdout.split(b"\x00")
            if (name := _decode_git_text(item, "Git reference metadata was malformed.").strip())
        ]
        return tuple(
            [await self.resolve_revision(repository_id, RevisionRef(name)) for name in names]
        )

    async def list_directory(
        self, revision: ResolvedRevision, path: RepositoryPath
    ) -> tuple[RepositoryEntry, ...]:
        """List one commit tree directory without recursively walking descendants."""
        root = self._root(revision.repository_id)
        treeish = revision.commit_sha
        if not path.is_root:
            treeish = f"{revision.commit_sha}:{path.as_posix()}"
        result = await self._runner.run(root, GitSubcommand.LS_TREE, "-z", "-l", treeish)
        entries = self._parse_tree(result.stdout, path)
        if len(entries) > self._policy.limits.max_directory_entries:
            raise LimitExceededError()
        return tuple(sorted(entries, key=lambda item: item.name.casefold()))

    async def read_file(self, revision: ResolvedRevision, path: RepositoryPath) -> FileContent:
        """Read a validated text blob from the commit object database."""
        if path.is_root:
            raise SecurityError("A file path is required.")
        reject_excluded_path(path.value)
        entry = await self._get_entry(revision, path)
        if entry.entry_type is RepositoryEntryType.SYMLINK:
            raise SecurityError("Symbolic links are not followed.")
        if entry.entry_type is RepositoryEntryType.SUBMODULE:
            raise SecurityError("Git submodules are unsupported.")
        if entry.entry_type is not RepositoryEntryType.FILE:
            raise RepositoryError("The requested path is not a file.")
        if await self._git_marks_binary(revision, path):
            raise BinaryFileError()
        if entry.size is not None and entry.size > self._policy.limits.max_text_file_bytes:
            raise LimitExceededError()
        result = await self._runner.run(
            self._root(revision.repository_id),
            GitSubcommand.CAT_FILE,
            "blob",
            entry.object_id,
            output_limit_bytes=self._policy.limits.max_text_file_bytes,
        )
        text, encoding = validate_text_bytes(path.value, result.stdout)
        return FileContent(
            repository_id=revision.repository_id,
            commit_sha=revision.commit_sha,
            path=path,
            content=text,
            encoding=encoding,
            byte_size=len(result.stdout),
            line_count=text.count("\n") + (1 if text else 0),
            content_hash=content_digest(result.stdout),
        )

    async def search_text(
        self, revision: ResolvedRevision, query: TextSearchQuery
    ) -> tuple[TextSearchHit, ...]:
        """Search a temporary SHA-pinned snapshot with ripgrep or a Python fallback."""
        maximum = min(query.max_results, self._policy.limits.max_search_results)
        try:
            with tempfile.TemporaryDirectory(prefix="codeatlas-") as temporary:
                snapshot = Path(temporary)
                snapshot.chmod(0o700)
                await self._materialize_snapshot(revision, query, snapshot)
                if shutil.which(self._ripgrep_executable):
                    return await self._search_ripgrep(revision, query, snapshot, maximum)
                if query.regex:
                    raise RepositoryError("Regex search requires ripgrep.")
                return self._search_python(revision, query, snapshot, maximum)
        except OSError as error:
            raise RepositoryError(
                "The temporary search snapshot was unavailable.", internal_cause=error
            ) from error

    async def get_commit(self, revision: ResolvedRevision) -> CommitInfo:
        """Return metadata for exactly one resolved commit."""
        result = await self._runner.run(
            self._root(revision.repository_id),
            GitSubcommand.SHOW,
            "-s",
            f"--format={_COMMIT_FORMAT}",
            revision.commit_sha,
        )
        commits = self._parse_commits(revision.repository_id, result.stdout)
        if len(commits) != 1:
            raise RepositoryError("Commit metadata was unavailable.")
        return commits[0]

    async def search_commits(
        self,
        repository_id: RepositoryId,
        query: str,
        *,
        revision: RevisionRef,
        author: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        path: RepositoryPath | None = None,
        max_results: int = 100,
    ) -> tuple[CommitInfo, ...]:
        """Search commit messages and optional author/path filters with a fixed format."""
        resolved = await self.resolve_revision(repository_id, revision)
        limit = min(max_results, self._policy.limits.max_search_results)
        arguments = [f"--format={_COMMIT_FORMAT}", f"--max-count={limit}", "--fixed-strings"]
        if query:
            arguments.append(f"--grep={query}")
        if author:
            arguments.append(f"--author={author}")
        if since:
            arguments.append(f"--since={since.isoformat()}")
        if until:
            arguments.append(f"--until={until.isoformat()}")
        arguments.append(resolved.commit_sha)
        if path is not None:
            arguments.extend(("--", path.as_posix()))
        result = await self._runner.run(self._root(repository_id), GitSubcommand.LOG, *arguments)
        return self._parse_commits(repository_id, result.stdout)

    async def get_file_history(
        self, revision: ResolvedRevision, path: RepositoryPath, *, max_results: int = 100
    ) -> tuple[FileHistoryEntry, ...]:
        """Return bounded first-parent-neutral file history with optional rename following."""
        arguments = [
            f"--format={_COMMIT_FORMAT}",
            f"--max-count={min(max_results, self._policy.limits.max_search_results)}",
        ]
        if self._policy.detect_renames:
            arguments.append("--follow")
        arguments.extend((revision.commit_sha, "--", path.as_posix()))
        result = await self._runner.run(
            self._root(revision.repository_id), GitSubcommand.LOG, *arguments
        )
        return tuple(
            FileHistoryEntry(commit, path)
            for commit in self._parse_commits(revision.repository_id, result.stdout)
        )

    async def get_blame(
        self, revision: ResolvedRevision, path: RepositoryPath
    ) -> tuple[BlameEntry, ...]:
        """Return line-level porcelain blame for one immutable file revision."""
        await self.read_file(revision, path)
        result = await self._runner.run(
            self._root(revision.repository_id),
            GitSubcommand.BLAME,
            "--line-porcelain",
            revision.commit_sha,
            "--",
            path.as_posix(),
        )
        return self._parse_blame(revision, path, result.stdout)

    async def compare_refs(self, base: ResolvedRevision, head: ResolvedRevision) -> DiffResult:
        """Return a bounded diff between two commits from the same repository."""
        if base.repository_id != head.repository_id:
            raise SecurityError("Cross-repository comparison is not permitted.")
        return await self._build_safe_diff(head, base.commit_sha, base.commit_sha)

    async def get_diff(self, revision: ResolvedRevision) -> DiffResult:
        """Return a commit diff against its first parent, or an empty tree for a root commit."""
        commit = await self.get_commit(revision)
        if commit.parent_shas:
            base = ResolvedRevision(
                revision.repository_id,
                commit.parent_shas[0],
                RevisionRef(commit.parent_shas[0]),
            )
            return await self.compare_refs(base, revision)
        return await self._build_safe_diff(revision, _EMPTY_TREE_SHA, None)

    async def _build_safe_diff(
        self,
        head: ResolvedRevision,
        git_base_sha: str,
        result_base_sha: str | None,
    ) -> DiffResult:
        root = self._root(head.repository_id)
        arguments = ["--name-status", "-z", "--no-ext-diff", "--no-textconv"]
        if self._policy.detect_renames:
            arguments.append("--find-renames")
        arguments.extend((git_base_sha, head.commit_sha, "--"))
        changed = await self._runner.run(root, GitSubcommand.DIFF, *arguments)
        chunks: list[bytes] = []
        excluded = 0
        for paths in self._parse_changed_paths(changed.stdout):
            if not await self._diff_paths_are_safe(head, git_base_sha, paths):
                excluded += 1
                continue
            diff_arguments = ["--no-ext-diff", "--no-textconv"]
            if self._policy.detect_renames:
                diff_arguments.append("--find-renames")
            diff_arguments.extend((git_base_sha, head.commit_sha, "--", *paths))
            result = await self._runner.run(
                root,
                GitSubcommand.DIFF,
                *diff_arguments,
                output_limit_bytes=self._policy.limits.max_diff_bytes,
            )
            chunks.append(result.stdout)
            if sum(len(chunk) for chunk in chunks) > self._policy.limits.max_diff_bytes:
                raise LimitExceededError()
        warnings = (f"{excluded} unsafe or binary diff entries were excluded.",) if excluded else ()
        return self._diff_result(head, b"".join(chunks), result_base_sha, warnings)

    @staticmethod
    def _parse_changed_paths(raw: bytes) -> tuple[tuple[str, ...], ...]:
        fields = [field for field in raw.split(b"\x00") if field]
        groups: list[tuple[str, ...]] = []
        index = 0
        while index < len(fields):
            status = fields[index].decode("ascii")
            index += 1
            path_count = 2 if status.startswith(("R", "C")) else 1
            if index + path_count > len(fields):
                raise RepositoryError("Diff metadata was malformed.")
            groups.append(
                tuple(
                    _decode_git_text(fields[index + offset], "Diff path metadata was malformed.")
                    for offset in range(path_count)
                )
            )
            index += path_count
        return tuple(groups)

    async def _diff_paths_are_safe(
        self,
        head: ResolvedRevision,
        base_sha: str,
        paths: tuple[str, ...],
    ) -> bool:
        base = ResolvedRevision(head.repository_id, base_sha, RevisionRef(base_sha))
        for path_text in paths:
            try:
                path = RepositoryPath(PurePosixPath(path_text))
                reject_excluded_path(path.value)
            except (ValueError, BinaryFileError, SecretDetectedError):
                return False
            found = False
            for revision in (base, head):
                try:
                    await self.read_file(revision, path)
                    found = True
                except RepositoryError:
                    continue
                except (BinaryFileError, SecretDetectedError, SecurityError, LimitExceededError):
                    return False
            if not found:
                return False
        return True

    def _root(self, repository_id: RepositoryId) -> Path:
        return self._registry.get(repository_id).canonical_location.path

    async def _git_text(self, root: Path, command: GitSubcommand, *arguments: str) -> str:
        result = await self._runner.run(root, command, *arguments)
        return _decode_git_text(result.stdout, "Git output was malformed.")

    def _enforce_repository_size(self, root: Path) -> None:
        total = 0
        try:
            for directory, names, files in os.walk(root, followlinks=False):
                names[:] = [name for name in names if not Path(directory, name).is_symlink()]
                for name in files:
                    candidate = Path(directory, name)
                    if not candidate.is_symlink():
                        total += candidate.stat().st_size
                    if total > self._policy.limits.max_repository_bytes:
                        raise LimitExceededError()
        except OSError as error:
            raise RepositoryError(internal_cause=error) from error

    def _parse_tree(self, raw: bytes, parent: RepositoryPath) -> list[RepositoryEntry]:
        entries: list[RepositoryEntry] = []
        for record in raw.split(b"\x00"):
            if not record:
                continue
            metadata, name_bytes = record.split(b"\t", 1)
            mode, object_type, object_id, size_text = metadata.split(maxsplit=3)
            name = _decode_git_text(name_bytes, "Git tree metadata was malformed.")
            child = PurePosixPath(name) if parent.is_root else parent.value / name
            if object_type == b"tree":
                entry_type = RepositoryEntryType.DIRECTORY
            elif object_type == b"commit":
                entry_type = RepositoryEntryType.SUBMODULE
            elif mode == b"120000":
                entry_type = RepositoryEntryType.SYMLINK
            else:
                entry_type = RepositoryEntryType.FILE
            entries.append(
                RepositoryEntry(
                    RepositoryPath(child),
                    name,
                    entry_type,
                    object_id.decode("ascii"),
                    None if size_text == b"-" else int(size_text),
                    "unsupported in M2" if entry_type is RepositoryEntryType.SUBMODULE else None,
                )
            )
        return entries

    async def _get_entry(self, revision: ResolvedRevision, path: RepositoryPath) -> RepositoryEntry:
        result = await self._runner.run(
            self._root(revision.repository_id),
            GitSubcommand.LS_TREE,
            "-z",
            "-l",
            revision.commit_sha,
            "--",
            path.as_posix(),
        )
        entries = self._parse_tree(result.stdout, RepositoryPath.root())
        exact = [entry for entry in entries if entry.path == path]
        if len(exact) != 1:
            raise RepositoryError("The requested repository path was not found.")
        return exact[0]

    async def _materialize_snapshot(
        self, revision: ResolvedRevision, query: TextSearchQuery, destination: Path
    ) -> None:
        result = await self._runner.run(
            self._root(revision.repository_id),
            GitSubcommand.LS_TREE,
            "-r",
            "-z",
            "-l",
            revision.commit_sha,
        )
        materialized_keys: set[str] = set()
        for entry in self._parse_tree(result.stdout, RepositoryPath.root()):
            if entry.entry_type is not RepositoryEntryType.FILE:
                continue
            if not query.path_prefix.is_root:
                try:
                    entry.path.value.relative_to(query.path_prefix.value)
                except ValueError:
                    continue
            if not self._matches_globs(entry.path.as_posix(), query):
                continue
            if entry.size is None or entry.size > self._policy.limits.max_search_file_bytes:
                continue
            try:
                reject_excluded_path(entry.path.value)
                if await self._git_marks_binary(revision, entry.path):
                    continue
                blob = await self._runner.run(
                    self._root(revision.repository_id),
                    GitSubcommand.CAT_FILE,
                    "blob",
                    entry.object_id,
                    output_limit_bytes=self._policy.limits.max_search_file_bytes,
                )
                validate_text_bytes(entry.path.value, blob.stdout)
            except (BinaryFileError, SecretDetectedError, LimitExceededError):
                continue
            identity = os.path.normcase(unicodedata.normalize("NFC", entry.path.as_posix()))
            if identity in materialized_keys:
                raise SecurityError("Repository paths collide on this filesystem.")
            materialized_keys.add(identity)
            target = destination.joinpath(*entry.path.value.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.stdout)
            target.chmod(0o600)

    @staticmethod
    def _matches_globs(path: str, query: TextSearchQuery) -> bool:
        if query.include_globs and not any(
            fnmatch.fnmatch(path, glob) for glob in query.include_globs
        ):
            return False
        return not any(fnmatch.fnmatch(path, glob) for glob in query.exclude_globs)

    async def _search_ripgrep(
        self,
        revision: ResolvedRevision,
        query: TextSearchQuery,
        snapshot: Path,
        maximum: int,
    ) -> tuple[TextSearchHit, ...]:
        arguments = ["--json", "--no-config", "--no-messages", "--color=never"]
        if not query.regex:
            arguments.append("--fixed-strings")
        if not query.case_sensitive:
            arguments.append("--ignore-case")
        arguments.extend(("--", query.pattern, str(snapshot)))
        environment = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"}
        for name in ("SYSTEMROOT", "COMSPEC", "WINDIR"):
            if value := os.environ.get(name):
                environment[name] = value
        process = await asyncio.create_subprocess_exec(
            self._ripgrep_executable,
            *arguments,
            cwd=snapshot,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RepositoryError("Text search streams were unavailable.")
        stdout_task = asyncio.create_task(
            self._read_limited_stream(process.stdout, self._policy.limits.max_command_output_bytes)
        )
        stderr_task = asyncio.create_task(
            self._read_limited_stream(process.stderr, self._policy.limits.max_command_output_bytes)
        )
        try:
            async with asyncio.timeout(self._policy.limits.command_timeout_seconds):
                stdout, _stderr = await asyncio.gather(stdout_task, stderr_task)
                await process.wait()
        except TimeoutError as error:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise RequestTimeoutError(internal_cause=error) from error
        except LimitExceededError:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        if process.returncode not in {0, 1}:
            raise RepositoryError("Text search failed.")
        hits: list[TextSearchHit] = []
        for line in stdout.splitlines():
            event = json.loads(line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            relative = Path(data["path"]["text"]).relative_to(snapshot).as_posix()
            excerpt = data["lines"]["text"].rstrip("\r\n")
            for submatch in data["submatches"]:
                start = self._utf8_byte_offset_to_character(excerpt, int(submatch["start"]))
                end = self._utf8_byte_offset_to_character(excerpt, int(submatch["end"]))
                hits.append(
                    self._search_hit(
                        revision,
                        RepositoryPath(PurePosixPath(relative)),
                        int(data["line_number"]),
                        excerpt,
                        start,
                        end,
                        snapshot,
                        query.context_lines,
                    )
                )
                if len(hits) >= maximum:
                    return tuple(hits)
        return tuple(hits)

    @staticmethod
    def _utf8_byte_offset_to_character(text: str, offset: int) -> int:
        """Convert a ripgrep UTF-8 byte offset to a Python character offset."""
        encoded = text.encode("utf-8")
        try:
            return len(encoded[:offset].decode("utf-8"))
        except UnicodeDecodeError as error:
            raise RepositoryError(
                "Text search output was malformed.", internal_cause=error
            ) from error

    @staticmethod
    async def _read_limited_stream(stream: asyncio.StreamReader, limit: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while chunk := await stream.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                raise LimitExceededError()
            chunks.append(chunk)
        return b"".join(chunks)

    async def _git_marks_binary(self, revision: ResolvedRevision, path: RepositoryPath) -> bool:
        result = await self._git_text(
            self._root(revision.repository_id),
            GitSubcommand.CHECK_ATTR,
            f"--source={revision.commit_sha}",
            "diff",
            "--",
            path.as_posix(),
        )
        return result.rstrip().endswith(": unset")

    def _search_python(
        self,
        revision: ResolvedRevision,
        query: TextSearchQuery,
        snapshot: Path,
        maximum: int,
    ) -> tuple[TextSearchHit, ...]:
        flags = 0 if query.case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(query.pattern), flags)
        hits: list[TextSearchHit] = []
        for file_path in sorted(path for path in snapshot.rglob("*") if path.is_file()):
            text = file_path.read_text(encoding="utf-8-sig")
            relative = RepositoryPath(PurePosixPath(file_path.relative_to(snapshot).as_posix()))
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in pattern.finditer(line):
                    hits.append(
                        self._search_hit(
                            revision,
                            relative,
                            line_number,
                            line,
                            match.start(),
                            match.end(),
                            snapshot,
                            query.context_lines,
                        )
                    )
                    if len(hits) >= maximum:
                        return tuple(hits)
        return tuple(hits)

    @staticmethod
    def _search_hit(
        revision: ResolvedRevision,
        path: RepositoryPath,
        line_number: int,
        excerpt: str,
        start: int,
        end: int,
        snapshot: Path,
        context_lines: int,
    ) -> TextSearchHit:
        lines = snapshot.joinpath(*path.value.parts).read_text(encoding="utf-8-sig").splitlines()
        before = tuple(lines[max(0, line_number - context_lines - 1) : line_number - 1])
        after = tuple(lines[line_number : line_number + context_lines])
        return TextSearchHit(
            revision.repository_id,
            revision.commit_sha,
            path,
            line_number,
            excerpt,
            start,
            end,
            context_before=before,
            context_after=after,
        )

    @staticmethod
    def _parse_commits(repository_id: RepositoryId, raw: bytes) -> tuple[CommitInfo, ...]:
        raw = raw.rstrip(b"\n")
        fields = raw.split(b"\x00")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 8:
            raise RepositoryError("Commit metadata was malformed.")
        commits: list[CommitInfo] = []
        for offset in range(0, len(fields), 8):
            sha, parents, author, email, authored, committed, subject, body = fields[
                offset : offset + 8
            ]
            sha = sha.lstrip(b"\n")
            try:
                commits.append(
                    CommitInfo(
                        repository_id,
                        sha.decode("ascii"),
                        tuple(parents.decode("ascii").split()),
                        author.decode("utf-8"),
                        email.decode("utf-8"),
                        datetime.fromisoformat(authored.decode("ascii")),
                        datetime.fromisoformat(committed.decode("ascii")),
                        subject.decode("utf-8"),
                        body.decode("utf-8").rstrip("\n"),
                    )
                )
            except (UnicodeDecodeError, ValueError) as error:
                raise RepositoryError(
                    "Commit metadata was malformed.", internal_cause=error
                ) from error
        return tuple(commits)

    @staticmethod
    def _parse_blame(
        revision: ResolvedRevision, path: RepositoryPath, raw: bytes
    ) -> tuple[BlameEntry, ...]:
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise RepositoryError("Blame metadata was malformed.", internal_cause=error) from error
        entries: list[BlameEntry] = []
        index = 0
        while index < len(lines):
            header = lines[index].split()
            if len(header) < 4 or _FULL_SHA.fullmatch(header[0]) is None:
                index += 1
                continue
            commit_sha, _original, final, count = header[:4]
            author = ""
            authored_at = datetime.fromtimestamp(0, UTC)
            content: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("\t"):
                if lines[index].startswith("author "):
                    author = lines[index][7:]
                elif lines[index].startswith("author-time "):
                    authored_at = datetime.fromtimestamp(int(lines[index][12:]), UTC)
                index += 1
            while index < len(lines) and lines[index].startswith("\t"):
                content.append(lines[index][1:])
                index += 1
            entries.append(
                BlameEntry(
                    revision.repository_id,
                    commit_sha,
                    path,
                    int(final),
                    int(count),
                    author,
                    authored_at,
                    tuple(content),
                )
            )
        return tuple(entries)

    @staticmethod
    def _diff_result(
        head: ResolvedRevision,
        raw: bytes,
        base_sha: str | None,
        warnings: tuple[str, ...] = (),
    ) -> DiffResult:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BinaryFileError(internal_cause=error) from error
        return DiffResult(
            head.repository_id,
            base_sha,
            head.commit_sha,
            text,
            len(raw),
            False,
            warnings,
        )
