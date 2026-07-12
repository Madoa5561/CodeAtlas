"""Text, binary, and basic credential exclusion for repository content."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import PurePosixPath

from codeatlas.domain.errors import BinaryFileError, SecretDetectedError

_BINARY_EXTENSIONS = frozenset(
    {
        ".7z",
        ".a",
        ".avi",
        ".bmp",
        ".class",
        ".db",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xz",
        ".zip",
    }
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
    }
)
_PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_TOKEN = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)"
    rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
)


def reject_excluded_path(path: PurePosixPath) -> None:
    """Reject known secret, archive, media, executable, and database paths."""
    lowered_name = path.name.casefold()
    if lowered_name in _SECRET_NAMES or lowered_name.startswith(".env."):
        raise SecretDetectedError()
    if path.suffix.casefold() in _BINARY_EXTENSIONS:
        raise BinaryFileError()
    if path.suffix.casefold() in {".key", ".p12", ".pfx", ".pem"}:
        raise SecretDetectedError()


def validate_text_bytes(path: PurePosixPath, content: bytes) -> tuple[str, str]:
    """Validate bytes as non-secret text and return decoded content and encoding."""
    reject_excluded_path(path)
    sample = content[:8192]
    if b"\x00" in sample:
        raise BinaryFileError()
    if _PRIVATE_KEY.search(sample) or _TOKEN.search(sample):
        raise SecretDetectedError()
    control_count = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    if sample and control_count / len(sample) > 0.05:
        raise BinaryFileError()
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig"), "utf-8-sig"
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as error:
        raise BinaryFileError(internal_cause=error) from error


def content_digest(content: bytes) -> str:
    """Return the stable SHA-256 digest of raw file bytes."""
    return sha256(content).hexdigest()
