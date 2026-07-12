"""Deterministic repository path validation and containment checks."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePosixPath

from codeatlas.domain.errors import SecurityError
from codeatlas.domain.repository_models import RepositoryPath, RepositoryPolicy

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_repository_path(
    value: str,
    policy: RepositoryPolicy,
    *,
    allow_empty: bool,
) -> RepositoryPath:
    """Validate input and return a normalized POSIX repository-relative path."""
    if "\x00" in value:
        raise SecurityError("Repository paths cannot contain NUL bytes.")
    if "\\" in value:
        raise SecurityError("Repository paths must use POSIX separators.")
    if value.startswith(("/", "//")) or _WINDOWS_DRIVE.match(value):
        raise SecurityError("Absolute repository paths are not permitted.")
    if value == "":
        if allow_empty:
            return RepositoryPath.root()
        raise SecurityError("A repository-relative path is required.")
    parts = PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Repository path traversal is not permitted.")
    lowered = tuple(part.casefold() for part in parts)
    if ".git" in lowered:
        raise SecurityError("Git metadata is not accessible.")
    if any(part in policy.deny_path_names for part in lowered):
        raise SecurityError("The repository path is excluded by policy.")
    return RepositoryPath(PurePosixPath(*parts))


def canonical_path_key(path: Path) -> str:
    """Return an OS-aware canonical key for duplicate detection."""
    resolved = unicodedata.normalize("NFC", str(path.resolve(strict=True)))
    return os.path.normcase(os.path.normpath(resolved))


def ensure_contained(root: Path, candidate: Path) -> Path:
    """Resolve a filesystem path and reject symlink/junction escape."""
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise SecurityError("Repository path escapes the registered root.") from error
    return resolved_candidate
