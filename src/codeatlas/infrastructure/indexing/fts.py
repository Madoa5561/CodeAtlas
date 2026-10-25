"""SQLite FTS5 maintenance and BM25 lexical search helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

FTS_TABLE = "code_search_fts"
FTS_SCHEMA_VERSION = "sqlite-fts5-v1"
_MAX_QUERY_TERMS = 12
_MAX_EXCERPT_CHARS = 4_000
_MAX_LINE_CHARS = 4_000
_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]{2,}")


@dataclass(frozen=True, slots=True)
class FtsRow:
    """One lexical search result row before domain conversion."""

    repository_id: str
    commit_sha: str
    path: str
    line: int
    excerpt: str
    score: float


def create_fts_schema(connection: Connection) -> None:
    """Create the generation-scoped line-level FTS5 table."""
    connection.exec_driver_sql(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
            content,
            generation_id UNINDEXED,
            path UNINDEXED,
            line UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )


def drop_fts_schema(connection: Connection) -> None:
    """Drop the FTS table during an explicit downgrade."""
    connection.exec_driver_sql(f"DROP TABLE IF EXISTS {FTS_TABLE}")


def _iter_lines(content: str) -> tuple[tuple[int, str], ...]:
    rows: list[tuple[int, str]] = []
    for number, line in enumerate(content.splitlines(), start=1):
        text_value = line[:_MAX_LINE_CHARS]
        if text_value.strip():
            rows.append((number, text_value))
    return tuple(rows)


def insert_file_fts(
    session: Session,
    *,
    generation_id: str,
    path: str,
    content: str,
) -> None:
    """Insert one file's non-empty lines into the lexical index."""
    rows = _iter_lines(content)
    if not rows:
        return
    session.execute(
        text(
            f"INSERT INTO {FTS_TABLE} (content, generation_id, path, line) "
            "VALUES (:content, :generation_id, :path, :line)"
        ),
        [
            {
                "content": line,
                "generation_id": generation_id,
                "path": path,
                "line": number,
            }
            for number, line in rows
        ],
    )


def copy_file_fts(
    session: Session,
    *,
    source_generation_id: str,
    target_generation_id: str,
    path: str,
) -> None:
    """Copy unchanged file lines into another generation."""
    session.execute(
        text(
            f"INSERT INTO {FTS_TABLE} (content, generation_id, path, line) "
            f"SELECT content, :target, path, line FROM {FTS_TABLE} "
            "WHERE generation_id = :source AND path = :path"
        ),
        {
            "target": target_generation_id,
            "source": source_generation_id,
            "path": path,
        },
    )


def delete_generation_fts(session: Session, generation_id: str) -> None:
    """Remove lexical rows owned by one generation."""
    session.execute(
        text(f"DELETE FROM {FTS_TABLE} WHERE generation_id = :generation_id"),
        {"generation_id": generation_id},
    )


def backfill_fts(connection: Connection) -> int:
    """Populate FTS rows from existing safe content during migration."""
    result = connection.execute(
        text(
            "SELECT f.generation_id, f.path, v.content_utf8 "
            "FROM indexed_files f JOIN file_versions v ON v.file_row_id = f.row_id "
            "WHERE v.content_utf8 IS NOT NULL"
        )
    )
    rows: list[dict[str, object]] = []
    for generation_id, path, content_utf8 in result:
        raw = bytes(content_utf8)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in _iter_lines(content):
            rows.append(
                {
                    "content": line,
                    "generation_id": generation_id,
                    "path": path,
                    "line": number,
                }
            )
        if len(rows) >= 10_000:
            connection.execute(
                text(
                    f"INSERT INTO {FTS_TABLE} (content, generation_id, path, line) "
                    "VALUES (:content, :generation_id, :path, :line)"
                ),
                rows,
            )
            rows.clear()
    if rows:
        connection.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (content, generation_id, path, line) "
                "VALUES (:content, :generation_id, :path, :line)"
            ),
            rows,
        )
    return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {FTS_TABLE}").scalar_one())


def build_match_expression(query: str) -> str | None:
    """Convert an untrusted query to a safe bounded FTS5 MATCH expression."""
    terms: list[str] = []
    for match in _TOKEN.finditer(query):
        term = match.group(0)
        if len(term) > 64:
            term = term[:64]
        quoted = '"' + term.replace('"', '""') + '"'
        if quoted not in terms:
            terms.append(quoted)
        if len(terms) >= _MAX_QUERY_TERMS:
            break
    if not terms:
        return None
    return " OR ".join(terms)


def search_fts(
    connection: Connection,
    *,
    generation_id: str,
    query: str,
    limit: int,
    path_prefix: str | None,
) -> tuple[FtsRow, ...]:
    """Return BM25-ranked line matches for one generation."""
    match_expression = build_match_expression(query)
    if match_expression is None or limit < 1:
        return ()
    sql = (
        f"SELECT g.repository_id, g.commit_sha, {FTS_TABLE}.path, {FTS_TABLE}.line, "
        f"{FTS_TABLE}.content, bm25({FTS_TABLE}) AS rank "
        f"FROM {FTS_TABLE} "
        f"JOIN index_generations g ON g.generation_id = {FTS_TABLE}.generation_id "
        f"WHERE {FTS_TABLE} MATCH :match AND {FTS_TABLE}.generation_id = :generation_id"
    )
    parameters: dict[str, object] = {
        "match": match_expression,
        "generation_id": generation_id,
        "limit": limit,
    }
    if path_prefix:
        sql += f" AND {FTS_TABLE}.path LIKE :path_prefix"
        parameters["path_prefix"] = f"{path_prefix}%"
    sql += f" ORDER BY rank, {FTS_TABLE}.path, CAST({FTS_TABLE}.line AS INTEGER) LIMIT :limit"
    rows = connection.execute(text(sql), parameters).all()
    results: list[FtsRow] = []
    for repository_id, commit_sha, path, line, content, rank in rows:
        excerpt = str(content)[:_MAX_EXCERPT_CHARS].strip()
        if not excerpt:
            continue
        results.append(
            FtsRow(
                repository_id=str(repository_id),
                commit_sha=str(commit_sha),
                path=str(path),
                line=int(line),
                excerpt=excerpt,
                score=max(0.0, -float(rank)),
            )
        )
    return tuple(results)
