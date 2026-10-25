"""Unit tests for the Discord block rendering and fence-safe chunking."""

from __future__ import annotations

import re
import unicodedata

import pytest

from codeatlas.adapters.discord.presentation import (
    DEFAULT_TRUNCATION_NOTE,
    MAX_RESPONSE_CHUNKS,
    labels_for,
    render_citation_footer,
    render_markdown,
    render_response,
    split_markdown,
)
from codeatlas.domain.interaction_models import (
    CanonicalResponse,
    Citation,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)

_FENCE_LINE = re.compile(r"^( {0,3})(`{3,})([^`]*)$")
_SHA = "0123456789abcdef0123456789abcdef01234567"


def _fence_balanced(chunk: str) -> bool:
    opening: str | None = None
    for line in chunk.split("\n"):
        match = _FENCE_LINE.match(line)
        if opening is None:
            if match is not None:
                opening = match.group(2)
            continue
        if match is not None and not match.group(3).strip() and len(match.group(2)) >= len(opening):
            opening = None
    return opening is None


def _block(
    kind: ResponseBlockKind,
    text: str,
    *,
    language: str | None = None,
    citation_ids: tuple[str, ...] = (),
) -> ResponseBlock:
    return ResponseBlock(
        kind=kind,
        text=text,
        language=language,
        citation_ids=citation_ids,
    )


def _citation(index: int, *, path: str = "src/app.ts", symbol: str | None = None) -> Citation:
    return Citation(
        citation_id=f"cit_{index}",
        repository_id="repo_main",
        commit_sha=_SHA,
        path=path,
        start_line=10 * index,
        end_line=10 * index + 4,
        retrieval_method="lexical",
        symbol_id=f"sym_{index}" if symbol is not None else None,
        symbol_name=symbol,
    )


def _response(
    *blocks: ResponseBlock,
    citations: tuple[Citation, ...] = (),
    status: ResponseStatus = ResponseStatus.OK,
    confidence: float = 0.75,
    uncertainties: tuple[str, ...] = (),
    search_methods: tuple[str, ...] = (),
    follow_ups: tuple[str, ...] = (),
) -> CanonicalResponse:
    return CanonicalResponse(
        status=status,
        blocks=blocks,
        citations=citations,
        confidence=confidence,
        uncertainties=uncertainties,
        search_methods=search_methods,
        follow_ups=follow_ups,
    )


def test_short_text_stays_in_one_chunk() -> None:
    text = "短い回答です。"
    assert split_markdown(text, max_chars=100) == (text,)


def test_empty_text_produces_no_chunks() -> None:
    assert split_markdown("", max_chars=100) == ()
    assert split_markdown("\n\n", max_chars=100) == ()


def test_character_limit_is_exact() -> None:
    text = "a" * 100
    assert split_markdown(text, max_chars=100) == (text,)

    chunks = split_markdown(text + "b", max_chars=100)
    assert len(chunks) == 2
    assert chunks[0] == text
    assert chunks[1] == "b"
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_crlf_is_normalized() -> None:
    assert split_markdown("a\r\nb", max_chars=100) == ("a\nb",)


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        split_markdown("text", max_chars=5)
    with pytest.raises(ValueError):
        split_markdown("text", max_chars=100, max_chunks=0)


def test_code_fence_is_closed_and_reopened() -> None:
    code = "\n".join(f"const value{i} = {i};" for i in range(40))
    text = f"```ts\n{code}\n```"

    chunks = split_markdown(text, max_chars=120)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert all(_fence_balanced(chunk) for chunk in chunks)
    assert chunks[0].startswith("```ts")
    assert all(chunk.startswith("```ts") for chunk in chunks[1:])
    for line in code.splitlines():
        assert sum(line in chunk for chunk in chunks) == 1


def test_fence_delimiters_are_never_partially_written() -> None:
    code = "\n".join(f"line {i} " + "x" * 30 for i in range(20))
    chunks = split_markdown(f"```python\n{code}\n```", max_chars=90)

    for chunk in chunks:
        for line in chunk.split("\n"):
            if "`" in line:
                assert _FENCE_LINE.match(line) is not None
                assert len(line) <= 90


def test_overlong_plain_line_is_hard_split() -> None:
    line = "x" * 250
    chunks = split_markdown(line, max_chars=100)

    assert len(chunks) == 3
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks) == line


def test_overlong_code_line_is_hard_split_inside_fence() -> None:
    code_line = "y" * 400
    chunks = split_markdown(f"```\n{code_line}\n```", max_chars=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert all(_fence_balanced(chunk) for chunk in chunks)
    code_chars = []
    for chunk in chunks:
        body = [line for line in chunk.split("\n") if not _FENCE_LINE.match(line)]
        code_chars.extend(body)
    assert "".join(code_chars) == code_line
    assert all("```" in chunk for chunk in chunks)


def test_unicode_and_combining_marks_are_not_orphaned() -> None:
    text = "a\u0301" * 60
    chunks = split_markdown(text, max_chars=21)

    assert all(len(chunk) <= 21 for chunk in chunks)
    assert "".join(chunks) == text
    for chunk in chunks:
        assert unicodedata.combining(chunk[0]) == 0


def test_emoji_are_kept_whole_per_character_budget() -> None:
    text = "🎉" * 50
    chunks = split_markdown(text, max_chars=20)

    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "".join(chunks) == text


def test_chunk_count_is_capped_with_truncation_note() -> None:
    text = "\n\n".join(f"paragraph {index} " + "z" * 40 for index in range(40))
    chunks = split_markdown(text, max_chars=120, max_chunks=3)

    assert len(chunks) == 3
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert chunks[-1].endswith(DEFAULT_TRUNCATION_NOTE)


def test_custom_truncation_note_is_applied() -> None:
    text = "\n\n".join(f"part {index} " + "q" * 40 for index in range(20))
    chunks = split_markdown(text, max_chars=100, max_chunks=2, truncation_note="[cut]")

    assert len(chunks) == 2
    assert chunks[-1].endswith("[cut]")


def test_truncation_never_breaks_a_code_fence() -> None:
    code = "\n".join(f"const value{index} = {index};" for index in range(400))
    response = _response(_block(ResponseBlockKind.CODE, code, language="ts"))

    chunks = render_response(response, max_chars=200, locale="ja", max_chunks=3)

    assert len(chunks) == 3
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert all(_fence_balanced(chunk) for chunk in chunks)
    assert chunks[-1].endswith(labels_for("ja").truncated)


def test_renders_text_bullets_and_code_text() -> None:
    response = _response(
        _block(ResponseBlockKind.TEXT, "結論です。"),
        _block(ResponseBlockKind.BULLETS, "first\n- second"),
        _block(ResponseBlockKind.CODE, "print('hi')\n", language="python"),
    )

    markdown = render_markdown(response)

    assert "結論です。" in markdown
    assert "- first\n- second" in markdown
    assert "```python\nprint('hi')\n```" in markdown


def test_code_fence_widens_for_embedded_fences() -> None:
    response = _response(
        _block(ResponseBlockKind.CODE, "```\ninner\n```\n"),
    )

    markdown = render_markdown(response)

    assert markdown.startswith("````\n")
    assert "````\n```\ninner\n```\n````" in markdown


def test_canonical_blocks_are_rendered_without_duplicate_footer() -> None:
    response = _response(
        _block(ResponseBlockKind.TEXT, "回答です。", citation_ids=("cit_1",)),
        _block(ResponseBlockKind.TEXT, "追加です。", citation_ids=("cit_2",)),
        citations=(_citation(1, path="src/first.ts", symbol="first"), _citation(2)),
    )

    markdown = render_markdown(response)
    footer = render_citation_footer(response)

    assert markdown == "回答です。\n\n追加です。"
    assert "**根拠**" not in markdown
    assert "**根拠**" in footer
    assert "`src/first.ts:10-14`" in footer
    assert "`src/app.ts:20-24`" in footer
    assert f"`{_SHA}`" in footer
    assert "`first`" in footer
    assert "`lexical`" in footer


def test_citation_footer_respects_limit() -> None:
    response = _response(
        _block(ResponseBlockKind.TEXT, "回答です。", citation_ids=("cit_1", "cit_2")),
        citations=(_citation(1), _citation(2), _citation(3)),
    )

    footer = render_citation_footer(response, max_citations=2)
    markdown = render_markdown(response, max_citations=2)

    assert "`src/app.ts:10-14`" in footer
    assert "`src/app.ts:20-24`" in footer
    assert "`src/app.ts:30-34`" not in footer
    assert markdown == "回答です。"


def test_english_locale_labels() -> None:
    response = _response(
        _block(ResponseBlockKind.TEXT, "Answer."),
        citations=(_citation(1),),
        uncertainties=("Not enough evidence.",),
        search_methods=("lexical",),
        follow_ups=("Where is it used?",),
    )

    markdown = render_markdown(response, locale="en")
    footer = render_citation_footer(response, locale="en")

    assert markdown == "Answer."
    assert "**Sources**" in footer
    assert "`lexical`" in footer
    assert "**Confidence**" not in markdown
    assert "**Uncertainties**" not in markdown
    assert "**Search methods**" not in markdown


def test_error_response_has_no_metadata_footer() -> None:
    response = CanonicalResponse(
        status=ResponseStatus.ERROR,
        blocks=(_block(ResponseBlockKind.TEXT, "安全なエラーです。"),),
        citations=(),
        safe_error="安全なエラーです。",
    )

    markdown = render_markdown(response)

    assert markdown == "安全なエラーです。"
    assert "確信度" not in markdown


def test_render_response_splits_with_translated_truncation_note() -> None:
    response = _response(
        *[_block(ResponseBlockKind.TEXT, f"段落{index} " + "w" * 80) for index in range(30)]
    )

    chunks = render_response(response, max_chars=200, locale="ja", max_chunks=2)

    assert len(chunks) == 2
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert chunks[-1].endswith(labels_for("ja").truncated)


def test_render_response_default_chunk_cap() -> None:
    response = _response(
        *[_block(ResponseBlockKind.TEXT, f"part {index} " + "v" * 120) for index in range(60)]
    )

    chunks = render_response(response, max_chars=200)

    assert len(chunks) == MAX_RESPONSE_CHUNKS
    assert all(len(chunk) <= 200 for chunk in chunks)
