"""Local Git repository fixtures for M2 integration and security tests."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


def run_git(root: Path, *arguments: str) -> str:
    """Run fixture setup Git commands without a shell or external network."""
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": os.environ.get("PATH", ""),
    }
    for name in ("SYSTEMROOT", "COMSPEC", "WINDIR"):
        if value := os.environ.get(name):
            environment[name] = value
    result = subprocess.run(
        ("git", *arguments),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return result.stdout.decode("utf-8").strip()


@dataclass(frozen=True, slots=True)
class GitRepositoryFixture:
    """Paths and immutable revisions from a temporary repository."""

    root: Path
    first_sha: str
    second_sha: str
    head_sha: str


@pytest.fixture
def git_repository(tmp_path: Path) -> GitRepositoryFixture:
    """Create a local repository containing representative safe and unsafe files."""
    root = tmp_path / "repository"
    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "CodeAtlas Test")
    run_git(root, "config", "user.email", "tests@example.invalid")

    (root / "src").mkdir()
    (root / "src" / "app.txt").write_text(
        "hello world\nneedle first\ncontext after\n", encoding="utf-8"
    )
    (root / "bom.txt").write_bytes(b"\xef\xbb\xbfBOM text\n")
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (root / ".gitattributes").write_text("attributes.bin -diff\n", encoding="utf-8")
    (root / "attributes.bin").write_text("attribute-marked text\n", encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial commit")
    first_sha = run_git(root, "rev-parse", "HEAD")
    run_git(root, "tag", "v1", first_sha)
    run_git(root, "branch", "feature", first_sha)

    run_git(root, "mv", "src/app.txt", "src/main.txt")
    (root / "src" / "main.txt").write_text(
        "hello world\nneedle second\ncontext after\n", encoding="utf-8"
    )
    (root / ".env").write_text("TOKEN=not-indexed\n", encoding="utf-8")
    (root / "private.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nprivate-material\n", encoding="utf-8"
    )
    (root / "credential.txt").write_text("api_key=abcdefghijklmnop123456\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"binary\x00content")
    (root / "invalid.txt").write_bytes(b"\xff\xfeinvalid")
    (root / "large.txt").write_text("L" * 4096, encoding="utf-8")

    run_git(root, "add", ".")
    run_git(
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{first_sha},modules/dependency",
    )
    link_blob_source = root / "link-source"
    link_blob_source.write_text("../outside.txt", encoding="utf-8")
    link_object = run_git(root, "hash-object", "-w", "link-source")
    link_blob_source.unlink()
    run_git(root, "update-index", "--add", "--cacheinfo", f"120000,{link_object},escape-link")
    run_git(root, "commit", "-m", "rename source and add fixtures")
    second_sha = run_git(root, "rev-parse", "HEAD")

    run_git(root, "checkout", "-b", "topic")
    (root / "topic.txt").write_text("topic change\n", encoding="utf-8")
    run_git(root, "add", "topic.txt")
    run_git(root, "commit", "-m", "topic commit")
    run_git(root, "checkout", "main")
    (root / "main-only.txt").write_text("main change\n", encoding="utf-8")
    run_git(root, "add", "main-only.txt")
    run_git(root, "commit", "-m", "main commit")
    run_git(root, "merge", "--no-ff", "topic", "-m", "merge topic")
    head_sha = run_git(root, "rev-parse", "HEAD")
    run_git(root, "branch", "ambiguous", first_sha)
    run_git(root, "tag", "ambiguous", second_sha)

    (root / "src" / "main.txt").write_text("working tree only\n", encoding="utf-8")
    return GitRepositoryFixture(root, first_sha, second_sha, head_sha)
