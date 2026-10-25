"""Discord markdown rendering and code-fence-safe response chunking.

The module is deliberately free of discord.py imports: it turns a channel-neutral
:class:`~codeatlas.domain.interaction_models.CanonicalResponse` into Discord
markdown strings and splits them under the configured character budget without
breaking code fences.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    Citation,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)

MAX_RESPONSE_CHUNKS = 8
"""Hard cap on the number of messages published for one answer (anti-spam)."""

DEFAULT_TRUNCATION_NOTE = "... (response truncated because it is too long)"
"""Fallback note when a caller does not supply a localized truncation note."""

_BULLET_PATTERN = re.compile(r"^(?:[-*+]|\d{1,3}[.)])\s+")
_FENCE_LINE_PATTERN = re.compile(r"^( {0,3})(`{3,})([^`]*)$")
_MIN_CHUNK_CHARS = 20
_COMBINING_MARK_RANGES = ("\ufe0e", "\ufe0f", "\u200d")


@dataclass(frozen=True, slots=True)
class _Labels:
    sources: str
    confidence: str
    uncertainties: str
    search_methods: str
    follow_ups: str
    truncated: str


_LABELS_JA = _Labels(
    sources="根拠",
    confidence="確信度",
    uncertainties="不明点",
    search_methods="検索方法",
    follow_ups="次の質問例",
    truncated="…(応答が長いため、残りの内容は省略されました)",
)
_LABELS_EN = _Labels(
    sources="Sources",
    confidence="Confidence",
    uncertainties="Uncertainties",
    search_methods="Search methods",
    follow_ups="Follow-up questions",
    truncated="... (response truncated because it is too long)",
)


def labels_for(locale: str) -> _Labels:
    """Return the footer labels for a channel locale."""
    return _LABELS_EN if locale.lower().startswith("en") else _LABELS_JA


def _inline(value: str, *, limit: int = 400) -> str:
    """Return a single-line, fence-safe rendering of dynamic text."""
    collapsed = " ".join(value.split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1] + "…"
    return collapsed.replace("`", "'")


def _render_markers(citation_ids: tuple[str, ...], indices: dict[str, int]) -> str:
    """Return numbered markers for callers that render an explicit source list."""
    markers = [
        f"[{indices[citation_id]}]" for citation_id in citation_ids if citation_id in indices
    ]
    return f" {' '.join(markers)}" if markers else ""


def _render_bullets(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line if _BULLET_PATTERN.match(line) else f"- {line}")
    return "\n".join(lines)


def _code_fence(text: str) -> str:
    """Choose a backtick fence longer than any fence-like line inside the code."""
    fence_length = 3
    for line in text.splitlines():
        match = _FENCE_LINE_PATTERN.match(line)
        if match is not None:
            fence_length = max(fence_length, len(match.group(2)) + 1)
    return "`" * fence_length


def _render_code_block(block: ResponseBlock) -> str:
    fence = _code_fence(block.text)
    language = block.language or ""
    body = block.text.strip("\n")
    return f"{fence}{language}\n{body}\n{fence}"


def _render_block(block: ResponseBlock) -> str:
    if block.kind is ResponseBlockKind.CODE:
        return _render_code_block(block)
    if block.kind is ResponseBlockKind.BULLETS:
        return _render_bullets(block.text)
    return block.text.strip()


def _render_citation_line(index: int, citation: Citation) -> str:
    repository = _inline(citation.repository_id)
    sha = _inline(citation.commit_sha)
    path = _inline(citation.path)
    details = [f"`{_inline(citation.retrieval_method)}`"]
    if citation.symbol_name:
        details.insert(0, f"`{_inline(citation.symbol_name)}`")
    return (
        f"{index}. `{repository}` @ `{sha}` — `{path}:{citation.start_line}-"
        f"{citation.end_line}` ({', '.join(details)})"
    )


def render_citation_footer(
    response: CanonicalResponse,
    *,
    locale: str = "ja",
    max_citations: int | None = None,
) -> str:
    """Render the numbered citation footer, capped for the channel budget."""
    labels = labels_for(locale)
    limit = _citation_limit(response, max_citations)
    if limit <= 0:
        return ""
    lines = [f"**{labels.sources}**"]
    lines.extend(
        _render_citation_line(index, citation)
        for index, citation in enumerate(response.citations[:limit], start=1)
    )
    return "\n".join(lines)


def _render_metadata_footer(response: CanonicalResponse, *, locale: str) -> str:
    if response.status in (ResponseStatus.ERROR, ResponseStatus.REJECTED):
        return ""
    labels = labels_for(locale)
    sections: list[str] = [f"**{labels.confidence}**: {response.confidence:.2f}"]
    if response.uncertainties:
        lines = [f"**{labels.uncertainties}**"]
        lines.extend(f"- {_inline(value)}" for value in response.uncertainties)
        sections.append("\n".join(lines))
    if response.search_methods:
        methods = ", ".join(f"`{_inline(value, limit=80)}`" for value in response.search_methods)
        sections.append(f"**{labels.search_methods}**: {methods}")
    if response.follow_ups:
        lines = [f"**{labels.follow_ups}**"]
        lines.extend(f"- {_inline(value)}" for value in response.follow_ups)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _citation_limit(response: CanonicalResponse, max_citations: int | None) -> int:
    if max_citations is None:
        return len(response.citations)
    if max_citations < 0:
        raise ValueError("max_citations must not be negative")
    return min(max_citations, len(response.citations))


def render_markdown(
    response: CanonicalResponse,
    *,
    locale: str = "ja",
    max_citations: int | None = None,
) -> str:
    """Render canonical blocks as Discord markdown.

    The canonical response already carries confidence, uncertainties, search
    methods and a compact citation section as blocks, so the presentation layer
    must not append a second footer. ``max_citations`` is retained for callers
    that still render the optional citation footer directly.
    """
    del max_citations
    return "\n\n".join(_render_block(block) for block in response.blocks if block.text.strip())


def _is_grapheme_extender(value: str) -> bool:
    return unicodedata.combining(value) != 0 or value in _COMBINING_MARK_RANGES


def _hard_split(line: str, limit: int) -> list[str]:
    """Split one overlong line by code points without orphaning combining marks."""
    if limit < 1:
        limit = 1
    pieces: list[str] = []
    remaining = line
    while len(remaining) > limit:
        cut = limit
        while cut > 1 and _is_grapheme_extender(remaining[cut]):
            cut -= 1
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    pieces.append(remaining)
    return pieces


def _split_balanced(text: str, max_chars: int) -> list[str]:
    """Split normalized markdown, closing and reopening fences at boundaries."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    open_line: str | None = None
    open_marker = ""

    def add_line(line: str) -> None:
        nonlocal current_len
        current_len += (1 if current else 0) + len(line)
        current.append(line)

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    def flush_open_fence() -> None:
        nonlocal current, current_len
        add_line(open_marker)
        chunks.append("\n".join(current))
        current = [open_line or open_marker]
        current_len = len(current[0])

    for line in text.split("\n"):
        if open_line is None:
            fence_match = _FENCE_LINE_PATTERN.match(line)
            if fence_match is not None:
                marker = fence_match.group(2)
                info = fence_match.group(3)
                reserve = len(marker) + 4
                available = max_chars - reserve
                opening = line
                if len(opening) > available:
                    opening = marker + info[: max(0, available - len(marker))]
                if current and current_len + 1 + len(opening) + reserve > max_chars:
                    flush()
                add_line(opening)
                open_line = opening
                open_marker = marker
                continue
            if current_len + (1 if current else 0) + len(line) <= max_chars:
                add_line(line)
                continue
            if len(line) <= max_chars:
                flush()
                add_line(line)
                continue
            flush()
            pieces = _hard_split(line, max_chars)
            chunks.extend(pieces[:-1])
            add_line(pieces[-1])
            continue

        close_match = _FENCE_LINE_PATTERN.match(line)
        is_close = (
            close_match is not None
            and not close_match.group(3).strip()
            and len(close_match.group(2)) >= len(open_marker)
        )
        if is_close:
            if current_len + (1 if current else 0) + len(line) <= max_chars:
                add_line(line)
                open_line = None
                open_marker = ""
                continue
            flush_open_fence()
            add_line(line)
            flush()
            open_line = None
            open_marker = ""
            continue

        if current_len + (1 if current else 0) + len(line) + len(open_marker) + 1 <= max_chars:
            add_line(line)
            continue

        if len(current) > 1:
            flush_open_fence()
        else:
            current = []
            current_len = 0
        available = max_chars - len(open_line or open_marker) - 2 - len(open_marker)
        if len(line) <= available:
            if not current:
                add_line(open_line or open_marker)
            add_line(line)
            continue
        pieces = _hard_split(line, max(1, available))
        for piece in pieces[:-1]:
            chunks.append("\n".join([open_line or open_marker, piece, open_marker]))
        if not current:
            add_line(open_line or open_marker)
        add_line(pieces[-1])

    if current:
        if open_line is not None:
            add_line(open_marker)
        flush()
    return chunks


def _trim_balanced(last: str, *, max_chars: int, suffix: str) -> str:
    """Trim the final chunk to make room for the note without breaking fences."""
    budget = max_chars - len(suffix)
    if budget <= 0:
        return suffix[:max_chars]
    lines = last.split("\n")
    lengths: list[int] = []
    open_markers: list[str | None] = []
    length = 0
    open_marker: str | None = None
    for index, line in enumerate(lines):
        length += len(line) + (1 if index else 0)
        match = _FENCE_LINE_PATTERN.match(line)
        if open_marker is None:
            if match is not None:
                open_marker = match.group(2)
        elif (
            match is not None
            and not match.group(3).strip()
            and len(match.group(2)) >= len(open_marker)
        ):
            open_marker = None
        lengths.append(length)
        open_markers.append(open_marker)
    for count in range(len(lines), 0, -1):
        marker = open_markers[count - 1]
        closing = len(marker) + 1 if marker is not None else 0
        if lengths[count - 1] + closing <= budget:
            prefix = "\n".join(lines[:count])
            if marker is not None:
                prefix += f"\n{marker}"
            return prefix + suffix
    return suffix[:max_chars]


def _apply_truncation(chunks: list[str], *, max_chars: int, truncation_note: str) -> list[str]:
    if not truncation_note:
        return chunks
    suffix = f"\n\n{truncation_note}"
    if len(suffix) >= max_chars:
        return [truncation_note[:max_chars]]
    last = chunks[-1]
    if len(last) + len(suffix) <= max_chars:
        chunks[-1] = last + suffix
    else:
        chunks[-1] = _trim_balanced(last, max_chars=max_chars, suffix=suffix)
    return chunks


def split_markdown(
    text: str,
    *,
    max_chars: int,
    max_chunks: int = MAX_RESPONSE_CHUNKS,
    truncation_note: str = DEFAULT_TRUNCATION_NOTE,
) -> tuple[str, ...]:
    """Split Discord markdown into balanced chunks of at most ``max_chars``.

    Fence delimiters are never split or dropped: when a chunk boundary falls
    inside a code block, the chunk is closed and the next chunk reopens the same
    language fence.  Overlong single lines are hard-split.  When the answer needs
    more than ``max_chunks`` chunks, the tail is dropped and a truncation note is
    appended to the last chunk.
    """
    if max_chars < _MIN_CHUNK_CHARS:
        raise ValueError(f"max_chars must be at least {_MIN_CHUNK_CHARS}")
    if max_chunks < 1:
        raise ValueError("max_chunks must be at least 1")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized:
        return ()
    chunks = _split_balanced(normalized, max_chars)
    if len(chunks) > max_chunks:
        chunks = _apply_truncation(
            chunks[:max_chunks],
            max_chars=max_chars,
            truncation_note=truncation_note,
        )
    return tuple(chunks)


def render_response(
    response: CanonicalResponse,
    *,
    max_chars: int = 1_900,
    locale: str = "ja",
    max_chunks: int = MAX_RESPONSE_CHUNKS,
    max_citations: int | None = None,
    truncation_note: str | None = None,
) -> tuple[str, ...]:
    """Render and split one canonical response for a Discord thread."""
    markdown = render_markdown(response, locale=locale, max_citations=max_citations)
    note = labels_for(locale).truncated if truncation_note is None else truncation_note
    return split_markdown(
        markdown,
        max_chars=max_chars,
        max_chunks=max_chunks,
        truncation_note=note,
    )


__all__ = [
    "DEFAULT_TRUNCATION_NOTE",
    "MAX_RESPONSE_CHUNKS",
    "labels_for",
    "render_citation_footer",
    "render_markdown",
    "render_response",
    "split_markdown",
]
