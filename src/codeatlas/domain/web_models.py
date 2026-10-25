"""Framework-independent safe web fetch models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebDocument:
    """Bounded text extracted from one public HTTP document."""

    url: str
    status: int
    content_type: str
    title: str | None
    text: str
    truncated: bool

    def __post_init__(self) -> None:
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("invalid web document URL")
        if self.status < 100 or self.status > 599:
            raise ValueError("invalid web document status")
        if len(self.text) > 100_000:
            raise ValueError("web document text is too large")
