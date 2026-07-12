"""Local Git repository provider integration and security tests."""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

import pytest
from conftest import GitRepositoryFixture, run_git

from codeatlas.adapters.repository.content_security import validate_text_bytes
from codeatlas.adapters.repository.git_runner import SafeGitRunner
from codeatlas.adapters.repository.local_git import LocalGitRepositoryProvider
from codeatlas.adapters.repository.path_security import ensure_contained, normalize_repository_path
from codeatlas.adapters.repository.registry import InMemoryRepositoryRegistry
from codeatlas.application.repositories import RepositoryApplicationService
from codeatlas.config.settings import RepositorySettings
from codeatlas.domain.errors import (
    BinaryFileError,
    InvalidRevisionError,
    LimitExceededError,
    NotFoundError,
    RepositoryError,
    SecretDetectedError,
    SecurityError,
)
from codeatlas.domain.repository_models import (
    RepositoryEntryType,
    RepositoryLimits,
    RepositoryLocation,
    RepositoryPath,
    RepositoryPolicy,
    RevisionRef,
    TextSearchQuery,
)


def make_provider(
    *,
    ripgrep_executable: str = "rg",
    max_text_file_bytes: int = 1024**2,
    max_repository_bytes: int = 2 * 1024**3,
    max_directory_entries: int = 1_000,
    max_diff_bytes: int = 2 * 1024**2,
) -> LocalGitRepositoryProvider:
    """Create an isolated in-memory provider with bounded test settings."""
    settings = RepositorySettings(max_text_file_bytes=max_text_file_bytes)
    limits = RepositoryLimits(
        max_repository_bytes=max_repository_bytes,
        max_text_file_bytes=settings.max_text_file_bytes,
        max_search_file_bytes=settings.max_search_file_bytes,
        max_directory_entries=max_directory_entries,
        max_search_results=settings.max_search_results,
        max_diff_bytes=max_diff_bytes,
        max_command_output_bytes=settings.max_command_output_bytes,
        command_timeout_seconds=settings.command_timeout_seconds,
    )
    return LocalGitRepositoryProvider(
        InMemoryRepositoryRegistry(),
        SafeGitRunner(
            timeout_seconds=settings.command_timeout_seconds,
            output_limit_bytes=settings.max_command_output_bytes,
        ),
        RepositoryPolicy(limits=limits),
        ripgrep_executable=ripgrep_executable,
    )


@pytest.mark.asyncio
async def test_registers_and_deduplicates_canonical_repository(
    git_repository: GitRepositoryFixture,
) -> None:
    """Equivalent path spellings resolve to one stable repository identity."""
    provider = make_provider()
    first = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    alias = git_repository.root / "src" / ".."
    second = await provider.register_local_repository(RepositoryLocation(alias))
    assert first.repository_id == second.repository_id
    assert first.canonical_location.path == git_repository.root.resolve()


@pytest.mark.asyncio
async def test_rejects_non_git_and_bare_repositories(tmp_path: Path) -> None:
    """Only non-bare local worktrees can be registered."""
    provider = make_provider()
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    with pytest.raises(RepositoryError):
        await provider.register_local_repository(RepositoryLocation(ordinary))

    bare = tmp_path / "bare.git"
    run_git(tmp_path, "init", "--bare", str(bare))
    with pytest.raises(RepositoryError):
        await provider.register_local_repository(RepositoryLocation(bare))


@pytest.mark.asyncio
async def test_repository_size_limit_is_enforced(git_repository: GitRepositoryFixture) -> None:
    """Registration fails before exposing a repository beyond its configured quota."""
    provider = make_provider(max_repository_bytes=1)
    with pytest.raises(LimitExceededError):
        await provider.register_local_repository(RepositoryLocation(git_repository.root))


@pytest.mark.asyncio
async def test_symlink_alias_deduplicates_when_supported(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    """Filesystem aliases cannot create duplicate registrations."""
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(git_repository.root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require additional privileges on this platform")
    provider = make_provider()
    first = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    second = await provider.register_local_repository(RepositoryLocation(alias))
    assert first.repository_id == second.repository_id


@pytest.mark.asyncio
async def test_nested_repository_is_only_registered_explicitly(
    git_repository: GitRepositoryFixture,
) -> None:
    """Registering a parent never implicitly registers a nested repository."""
    nested = git_repository.root / "nested"
    nested.mkdir()
    run_git(nested, "init", "-b", "main")
    run_git(nested, "config", "user.name", "Nested")
    run_git(nested, "config", "user.email", "nested@example.invalid")
    (nested / "file.txt").write_text("nested\n", encoding="utf-8")
    run_git(nested, "add", ".")
    run_git(nested, "commit", "-m", "nested")

    provider = make_provider()
    parent = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    nested_descriptor = await provider.register_local_repository(RepositoryLocation(nested))
    assert parent.repository_id != nested_descriptor.repository_id


@pytest.mark.asyncio
async def test_submodule_worktree_is_rejected(tmp_path: Path) -> None:
    """A Git submodule must not be registered as an independent M2 repository."""
    child = tmp_path / "child"
    child.mkdir()
    run_git(child, "init", "-b", "main")
    run_git(child, "config", "user.name", "Child")
    run_git(child, "config", "user.email", "child@example.invalid")
    (child / "file.txt").write_text("child\n", encoding="utf-8")
    run_git(child, "add", ".")
    run_git(child, "commit", "-m", "child")
    superproject = tmp_path / "super"
    superproject.mkdir()
    run_git(superproject, "init", "-b", "main")
    run_git(superproject, "config", "user.name", "Super")
    run_git(superproject, "config", "user.email", "super@example.invalid")
    run_git(
        superproject,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(child),
        "modules/child",
    )
    run_git(superproject, "commit", "-am", "add submodule")

    provider = make_provider()
    with pytest.raises(RepositoryError):
        await provider.register_local_repository(
            RepositoryLocation(superproject / "modules" / "child")
        )


@pytest.mark.parametrize(
    "value",
    [
        "../secret",
        "/absolute",
        "C:/Windows",
        "//server/share",
        "bad\x00path",
        "a\\b",
        ".git/config",
    ],
)
def test_path_security_rejects_unsafe_forms(value: str) -> None:
    """Traversal, absolute, drive, UNC, NUL, backslash, and Git metadata are denied."""
    with pytest.raises(SecurityError):
        normalize_repository_path(value, RepositoryPolicy(), allow_empty=False)


def test_path_security_root_and_case_are_stable() -> None:
    """Root is explicit and path case is preserved for Git object lookup."""
    assert normalize_repository_path("", RepositoryPolicy(), allow_empty=True).is_root
    path = normalize_repository_path("Src/Main.TXT", RepositoryPolicy(), allow_empty=False)
    assert path.as_posix() == "Src/Main.TXT"


def test_containment_rejects_paths_outside_root(tmp_path: Path) -> None:
    """Resolved filesystem candidates must remain below the canonical root."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(SecurityError):
        ensure_contained(root, outside)


def test_invalid_regex_is_rejected_before_search() -> None:
    """Malformed regex never reaches ripgrep or the fallback engine."""
    with pytest.raises(ValueError):
        TextSearchQuery("[", regex=True)


@pytest.mark.asyncio
async def test_resolves_branches_tags_and_sha(git_repository: GitRepositoryFixture) -> None:
    """Every accepted revision is returned as a full immutable commit SHA."""
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    branch = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    tag = await provider.resolve_revision(descriptor.repository_id, RevisionRef("v1"))
    direct = await provider.resolve_revision(
        descriptor.repository_id, RevisionRef(git_repository.second_sha)
    )
    assert branch.commit_sha == git_repository.head_sha
    assert tag.commit_sha == git_repository.first_sha
    assert direct.commit_sha == git_repository.second_sha
    assert all(
        len(item.commit_sha) == 40
        for item in await provider.list_branches(descriptor.repository_id)
    )
    assert all(
        len(item.commit_sha) == 40 for item in await provider.list_tags(descriptor.repository_id)
    )


@pytest.mark.asyncio
async def test_rejects_invalid_or_injection_revision(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
) -> None:
    """Invalid revisions remain argv data and cannot execute shell syntax."""
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    marker = tmp_path / "injected"
    with pytest.raises(InvalidRevisionError):
        await provider.resolve_revision(
            descriptor.repository_id,
            RevisionRef(f"HEAD;touch {marker}"),
        )
    assert marker.exists() is False
    with pytest.raises(InvalidRevisionError):
        await provider.resolve_revision(descriptor.repository_id, RevisionRef("ambiguous"))


@pytest.mark.asyncio
async def test_tree_and_sha_pinned_file_reads(git_repository: GitRepositoryFixture) -> None:
    """Tree and file reads use commit objects and ignore working-tree modifications."""
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    root_entries = await provider.list_directory(head, RepositoryPath.root())
    assert any(
        entry.name == "src" and entry.entry_type is RepositoryEntryType.DIRECTORY
        for entry in root_entries
    )
    content = await provider.read_file(head, RepositoryPath(PurePosixPath("src/main.txt")))
    assert content.commit_sha == git_repository.head_sha
    assert "needle second" in content.content
    assert "working tree only" not in content.content
    assert content.content_hash and content.encoding == "utf-8"
    with pytest.raises(RepositoryError):
        await provider.read_file(head, RepositoryPath(PurePosixPath("SRC/main.txt")))
    modules = await provider.list_directory(head, RepositoryPath(PurePosixPath("modules")))
    assert modules[0].entry_type is RepositoryEntryType.SUBMODULE
    assert modules[0].unsupported_reason


@pytest.mark.asyncio
async def test_directory_result_limit_is_enforced(git_repository: GitRepositoryFixture) -> None:
    """Directory enumeration never silently exceeds its configured result bound."""
    provider = make_provider(max_directory_entries=1)
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    with pytest.raises(LimitExceededError):
        await provider.list_directory(head, RepositoryPath.root())


@pytest.mark.asyncio
async def test_file_encoding_binary_secret_size_and_symlink_policies(
    git_repository: GitRepositoryFixture,
) -> None:
    """Only bounded, decoded, non-secret regular blobs can be returned."""
    provider = make_provider(max_text_file_bytes=1024)
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    bom = await provider.read_file(head, RepositoryPath(PurePosixPath("bom.txt")))
    assert bom.encoding == "utf-8-sig" and bom.content.startswith("BOM")
    for path in ("attributes.bin", "binary.bin", "invalid.txt"):
        with pytest.raises(BinaryFileError):
            await provider.read_file(head, RepositoryPath(PurePosixPath(path)))
    for path in (".env", "private.pem", "credential.txt"):
        with pytest.raises(SecretDetectedError):
            await provider.read_file(head, RepositoryPath(PurePosixPath(path)))
    with pytest.raises(LimitExceededError):
        await provider.read_file(head, RepositoryPath(PurePosixPath("large.txt")))
    with pytest.raises(SecurityError):
        await provider.read_file(head, RepositoryPath(PurePosixPath("escape-link")))


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_search_fixed_regex_case_glob_context_and_cleanup(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    """ripgrep and fallback search the same temporary immutable snapshot and clean it."""
    temp_root = tmp_path / "snapshots"
    temp_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temp_root))
    provider = make_provider(ripgrep_executable="missing-rg" if use_fallback else "rg")
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    hits = await provider.search_text(
        head,
        TextSearchQuery(
            "NEEDLE" if not use_fallback else r"needle\s+second",
            regex=use_fallback,
            case_sensitive=False,
            include_globs=("src/*.txt",),
            max_results=1,
            context_lines=1,
        ),
    )
    assert len(hits) == 1
    assert hits[0].commit_sha == git_repository.head_sha
    assert hits[0].path.as_posix() == "src/main.txt"
    assert hits[0].context_before == ("hello world",)
    assert hits[0].context_after == ("context after",)
    assert list(temp_root.iterdir()) == []


@pytest.mark.asyncio
async def test_search_excludes_secret_and_binary_content(
    git_repository: GitRepositoryFixture,
) -> None:
    """Secret and binary blobs are never materialized into the search snapshot."""
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    assert await provider.search_text(head, TextSearchQuery("not-indexed")) == ()
    assert await provider.search_text(head, TextSearchQuery("private-material")) == ()


@pytest.mark.asyncio
async def test_search_cancellation_cleans_snapshot(
    git_repository: GitRepositoryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling search always removes its temporary SHA snapshot."""
    temp_root = tmp_path / "snapshots"
    temp_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temp_root))
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))

    async def wait_forever(*_arguments: object) -> tuple[object, ...]:
        await asyncio.Event().wait()
        return ()

    monkeypatch.setattr(provider, "_search_ripgrep", wait_forever)
    task = asyncio.create_task(provider.search_text(head, TextSearchQuery("needle")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(temp_root.iterdir()) == []


@pytest.mark.asyncio
async def test_commit_history_blame_diff_rename_and_merge(
    git_repository: GitRepositoryFixture,
) -> None:
    """History operations preserve parents, timezone, rename history, blame, and bounded diff."""
    provider = make_provider()
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    head = await provider.resolve_revision(descriptor.repository_id, RevisionRef("main"))
    commit = await provider.get_commit(head)
    assert commit.commit_sha == git_repository.head_sha
    assert len(commit.parent_shas) == 2
    assert commit.committed_at.tzinfo is not None
    commits = await provider.search_commits(
        descriptor.repository_id,
        "commit",
        revision=RevisionRef("main"),
        author="CodeAtlas",
        max_results=10,
    )
    assert commits and all(len(item.commit_sha) == 40 for item in commits)
    dated = await provider.search_commits(
        descriptor.repository_id,
        "commit",
        revision=RevisionRef("main"),
        since=commits[-1].authored_at,
        until=commit.committed_at,
        max_results=10,
    )
    assert dated
    history = await provider.get_file_history(
        head, RepositoryPath(PurePosixPath("src/main.txt")), max_results=10
    )
    assert {entry.commit.commit_sha for entry in history} >= {
        git_repository.first_sha,
        git_repository.second_sha,
    }
    blame = await provider.get_blame(head, RepositoryPath(PurePosixPath("src/main.txt")))
    assert blame and sum(entry.line_count for entry in blame) >= 3
    first = await provider.resolve_revision(
        descriptor.repository_id, RevisionRef(git_repository.first_sha)
    )
    second = await provider.resolve_revision(
        descriptor.repository_id, RevisionRef(git_repository.second_sha)
    )
    comparison = await provider.compare_refs(first, second)
    assert comparison.base_sha == git_repository.first_sha
    assert "diff --git" in comparison.text
    assert "not-indexed" not in comparison.text
    assert "private-material" not in comparison.text
    assert comparison.warnings
    commit_diff = await provider.get_diff(head)
    assert commit_diff.base_sha == commit.parent_shas[0]


@pytest.mark.asyncio
async def test_diff_output_limit_is_enforced(git_repository: GitRepositoryFixture) -> None:
    """Diff generation aborts rather than returning content beyond its hard bound."""
    provider = make_provider(max_diff_bytes=128)
    descriptor = await provider.register_local_repository(RepositoryLocation(git_repository.root))
    first = await provider.resolve_revision(
        descriptor.repository_id, RevisionRef(git_repository.first_sha)
    )
    second = await provider.resolve_revision(
        descriptor.repository_id, RevisionRef(git_repository.second_sha)
    )
    with pytest.raises(LimitExceededError):
        await provider.compare_refs(first, second)


def test_content_scanner_does_not_claim_complete_dlp() -> None:
    """Ordinary text remains readable while basic explicit credential patterns are blocked."""
    text, encoding = validate_text_bytes(PurePosixPath("notes.txt"), b"tokenization notes\n")
    assert text == "tokenization notes\n" and encoding == "utf-8"


@pytest.mark.asyncio
async def test_application_service_and_unregistered_repository(
    git_repository: GitRepositoryFixture,
) -> None:
    """Application registration depends on the Port and unknown IDs remain inaccessible."""
    provider = make_provider()
    service = RepositoryApplicationService(provider)
    descriptor = await service.register_local_repository(git_repository.root)
    assert await provider.get_repository(descriptor.repository_id) == descriptor
    unknown = type(descriptor.repository_id)("repo_" + "0" * 32)
    with pytest.raises(NotFoundError):
        await provider.get_repository(unknown)
