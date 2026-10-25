"""Deterministic, locale-aware text analysis shared by the agent pipeline stages."""

from __future__ import annotations

import re
from dataclasses import dataclass

_QUOTED = re.compile(
    r"`([^`\n]{1,200})`"
    r'|"([^"\n]{1,200})"'
    r"|'([^'\n]{1,200})'"
    r"|「([^」\n]{1,200})」"
    r"|『([^』\n]{1,200})』"
)
_CODE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_PATH_TOKEN = re.compile(
    r"(?:[A-Za-z0-9_.@+-]+/)+[A-Za-z0-9_.@+*?\[\]-]*"
    r"|[A-Za-z0-9_.@+-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|toml|ya?ml|sql|css|"
    r"html|sh|txt|lock|cfg|ini|go|rs|java|kt|rb|php|c|h|cpp|hpp|cs|swift)"
)
_SHA_TOKEN = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_INLINE_CITATION = re.compile(r"\[[^\[\]\n]{0,200}:\d{1,6}(?:-\d{1,6})?\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

_EN_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "what",
        "where",
        "when",
        "which",
        "who",
        "whom",
        "how",
        "why",
        "does",
        "did",
        "done",
        "is",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "as",
        "an",
        "or",
        "not",
        "can",
        "could",
        "should",
        "would",
        "you",
        "your",
        "please",
        "tell",
        "show",
        "explain",
        "describe",
        "help",
        "find",
        "search",
        "look",
        "about",
        "with",
        "this",
        "that",
        "these",
        "those",
        "from",
        "into",
        "over",
        "under",
        "there",
        "here",
        "have",
        "has",
        "had",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "code",
        "file",
        "files",
        "repository",
        "repositories",
        "repo",
        "project",
        "source",
        "want",
        "need",
        "know",
        "give",
        "list",
    }
)

_HISTORY_KEYWORDS = (
    "history",
    "commit",
    "commits",
    "git log",
    "blame",
    "last changed",
    "who changed",
    "when was",
    "changelog",
    "履歴",
    "コミット",
    "変更履歴",
    "いつ変更",
    "誰が変更",
    "最終変更",
)
_DIFF_KEYWORDS = (
    "diff",
    "difference",
    "changes between",
    "what changed",
    "compare",
    "差分",
    "比較",
    "変更点",
    "変更内容",
)
_CALLERS_KEYWORDS = (
    "caller",
    "callers",
    "who calls",
    "calls into",
    "callsite",
    "呼び出し元",
    "呼び出し",
    "呼んでいる",
)
_REFERENCES_KEYWORDS = (
    "reference",
    "references",
    "referenced",
    "referencing",
    "usage",
    "usages",
    "used by",
    "where used",
    "参照",
    "使用箇所",
    "使われ",
    "利用箇所",
)
_DEFINITION_KEYWORDS = (
    "definition",
    "defined",
    "define",
    "declaration",
    "declared",
    "implemented",
    "implementation",
    "定義",
    "宣言",
    "実装",
)
_DIRECTORY_KEYWORDS = (
    "directory",
    "directories",
    "folder",
    "tree",
    "structure",
    "layout",
    "files in",
    "ディレクトリ",
    "フォルダ",
    "構成",
    "構造",
    "一覧",
    "ツリー",
)
_DEPENDENCY_KEYWORDS = (
    "dependency",
    "dependencies",
    "depends on",
    "imports",
    "imported",
    "依存",
    "依存関係",
    "読み込",
)
_CALLEES_KEYWORDS = (
    "callee",
    "callees",
    "calls from",
    "what does it call",
    "呼び出し先",
)
_DEPENDENTS_KEYWORDS = (
    "dependent",
    "dependents",
    "who depends",
    "被依存",
    "参照元",
)
_BLAME_KEYWORDS = (
    "blame",
    "who wrote",
    "who authored",
    "who last touched",
    "誰が書いた",
    "作者",
)
_REPOSITORY_KEYWORDS = (
    "repositories",
    "registered repositories",
    "repository list",
    "list repositories",
    "リポジトリ一覧",
    "登録済みリポジトリ",
)
_EXPORT_KEYWORDS = (
    "export",
    "exports",
    "exported",
    "re-export",
    "public api",
    "entrypoint",
    "entry point",
    "エクスポート",
    "再エクスポート",
    "外部公開",
    "公開されている",
    "エントリポイント",
    "エントリーポイント",
)
_GRAPH_KEYWORDS = (
    "call graph",
    "calls graph",
    "dependency graph",
    "dependencies graph",
    "import graph",
    "graph traversal",
    "traverse",
    "呼び出しグラフ",
    "依存グラフ",
    "参照グラフ",
    "グラフ",
    "影響範囲",
)
_PULL_REQUEST_KEYWORDS = (
    "pull request",
    "pr ",
    "プルリク",
    "プルリクエスト",
    "pr review",
    "review pr",
    "コードレビュー",
    "レビュー",
)
_ISSUE_KEYWORDS = ("issue", "issues", "イシュー", "課題")
_GITHUB_NUMBER = re.compile(
    r"(?:#|(?:pr|pull\s+request|issue|プルリク(?:エスト)?|イシュー)\s*#?)(\d{1,7})",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)

_ASCII_KEYWORD = re.compile(r"^[a-z0-9 ]+$")

# Common Japanese/English product terms mapped to the identifiers used in source code.
# The expansion is intentionally small and deterministic; it is not a translation service.
_DOMAIN_SYNONYMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("認証", "authentication", "authorization", "ログイン", "login"), ("auth", "login")),
    (("設定", "configuration", "settings"), ("config", "settings")),
    (("依存", "dependency", "dependencies"), ("import", "dependency")),
    (("初期化", "initialization", "initialize"), ("init", "initialize")),
    (("リクエスト", "request", "http"), ("request", "http")),
    (("エラー", "error", "exception"), ("error", "exception")),
    (("テスト", "test", "spec"), ("test", "spec")),
    (("型定義", "型", "type", "interface"), ("type", "interface")),
    (("イベント", "event"), ("event", "events")),
    (("メッセージ", "message"), ("message", "messages")),
    (("暗号", "encrypt", "crypto"), ("crypto", "encrypt")),
    (("接続", "connection", "connect"), ("connection", "connect")),
    (("コールバック", "callback"), ("callback",)),
    (("スレッド", "thread"), ("thread",)),
    (("タイムアウト", "timeout"), ("timeout",)),
    (("再試行", "retry"), ("retry",)),
    (("ログ出力", "ログファイル", "logging"), ("log", "logger")),
    (("キャッシュ", "cache"), ("cache",)),
    (("キュー", "queue"), ("queue",)),
    (("ファイル", "file"), ("file",)),
)


def expand_domain_terms(query: str) -> tuple[str, ...]:
    """Map well-known Japanese/English domain terms to likely source identifiers."""
    folded = query.casefold()
    terms: list[str] = []
    for triggers, expansions in _DOMAIN_SYNONYMS:
        if any(_contains_keyword(folded, trigger.casefold()) for trigger in triggers):
            terms.extend(expansions)
    return _dedupe(terms)


def is_japanese(locale: str) -> bool:
    """Return whether a locale selects Japanese presentation."""
    normalized = locale.strip().lower()
    return normalized.startswith("ja") or normalized.startswith("jp")


def localized(locale: str, *, ja: str, en: str) -> str:
    """Select the Japanese or English variant of a short message."""
    return ja if is_japanese(locale) else en


def _contains_keyword(text: str, keyword: str) -> bool:
    if _ASCII_KEYWORD.fullmatch(keyword) is not None:
        pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])")
        return pattern.search(text) is not None
    return keyword in text


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(_contains_keyword(text, keyword) for keyword in keywords)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        ordered.append(value)
    return tuple(ordered)


def extract_quoted_phrases(query: str) -> tuple[str, ...]:
    """Extract backticked, quoted, and bracket-quoted phrases in source order."""
    phrases: list[str] = []
    for match in _QUOTED.finditer(query):
        for group in match.groups():
            if group is not None and group.strip():
                phrases.append(group.strip())
                break
    return _dedupe(phrases)


def _looks_like_identifier(token: str) -> bool:
    if len(token) < 3 or len(token) > 200:
        return False
    if token.casefold() in _EN_STOPWORDS:
        return False
    if "_" in token or "." in token:
        return True
    has_upper = any(character.isupper() for character in token)
    has_lower = any(character.islower() for character in token)
    return has_upper and has_lower


def extract_identifiers(query: str) -> tuple[str, ...]:
    """Extract likely symbol identifiers, including quoted code fragments."""
    identifiers: list[str] = []
    for phrase in extract_quoted_phrases(query):
        token = phrase.strip().strip(".,;:!?()[]{}<>")
        if token and len(token) <= 200:
            identifiers.append(token)
    for match in _CODE_TOKEN.finditer(query):
        token = match.group(0).strip(".")
        if _looks_like_identifier(token):
            identifiers.append(token)
    return _dedupe(identifiers)[:12]


def _normalize_path(token: str) -> str | None:
    candidate = token.strip().strip("`\"'")
    candidate = candidate.replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.rstrip(".,;:!?")
    if not candidate or candidate.startswith("/") or ".." in candidate.split("/"):
        return None
    return candidate


def extract_paths(query: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (files, globs, directories) referenced by the question."""
    files: list[str] = []
    globs: list[str] = []
    directories: list[str] = []
    for match in _PATH_TOKEN.finditer(query):
        normalized = _normalize_path(match.group(0))
        if normalized is None:
            continue
        if "*" in normalized or "?" in normalized or "[" in normalized:
            globs.append(normalized)
        elif normalized.endswith("/") or match.group(0).endswith("/"):
            directories.append(normalized.rstrip("/") or ".")
        else:
            files.append(normalized)
    return _dedupe(files), _dedupe(globs), _dedupe(directories)


def extract_sha(query: str) -> str | None:
    """Return the first full commit SHA mentioned in the question."""
    match = _SHA_TOKEN.search(query)
    return match.group(0).lower() if match is not None else None


def extract_terms(query: str) -> tuple[str, ...]:
    """Extract lowercase lexical terms used for overlap scoring."""
    terms: list[str] = []
    for match in _WORD.finditer(query):
        term = match.group(0).casefold()
        if len(term) < 2:
            continue
        if term.isascii():
            base = term.split(".")[0]
            if len(base) < 3 or base in _EN_STOPWORDS:
                continue
        terms.append(term)
    return _dedupe(terms)[:24]


@dataclass(frozen=True, slots=True)
class QuerySignals:
    """Deterministic interpretation of one natural-language question."""

    query: str
    identifiers: tuple[str, ...]
    quoted: tuple[str, ...]
    files: tuple[str, ...]
    globs: tuple[str, ...]
    directories: tuple[str, ...]
    terms: tuple[str, ...]
    commit_sha: str | None
    wants_history: bool
    wants_diff: bool
    wants_callers: bool
    wants_callees: bool
    wants_references: bool
    wants_definition: bool
    wants_directory: bool
    wants_dependencies: bool
    wants_dependents: bool
    wants_blame: bool
    wants_repository_list: bool
    wants_exports: bool
    wants_graph: bool
    wants_pull_request: bool
    wants_issue: bool
    github_number: int | None
    urls: tuple[str, ...]

    @property
    def search_phrase(self) -> str:
        """Return the best single string to send to a lexical search tool."""
        candidates: tuple[str, ...] = (
            *self.quoted,
            *self.identifiers,
            *self.files,
            *self.directories,
        )
        for candidate in candidates:
            if candidate.strip():
                return candidate.strip()[:200]
        if self.commit_sha is not None:
            return self.commit_sha
        collapsed = _WHITESPACE.sub(" ", self.query).strip()
        return collapsed[:200] if collapsed else self.query[:200]

    @property
    def has_focus(self) -> bool:
        """Return whether the question names a symbol, phrase, or path."""
        return bool(self.identifiers or self.quoted or self.files or self.globs or self.directories)

    @property
    def wants_intent_tool(self) -> bool:
        """Return whether any specialized (non-lexical) intent was detected."""
        return any(
            (
                self.wants_history,
                self.wants_diff,
                self.wants_callers,
                self.wants_callees,
                self.wants_references,
                self.wants_definition,
                self.wants_directory,
                self.wants_dependencies,
                self.wants_dependents,
                self.wants_blame,
                self.wants_repository_list,
                self.wants_exports,
                self.wants_graph,
                self.wants_pull_request,
                self.wants_issue,
                self.github_number,
                self.urls,
            )
        )


def analyze(query: str) -> QuerySignals:
    """Derive locale-independent investigation signals from a question."""
    folded = query.casefold()
    files, globs, directories = extract_paths(query)
    path_tokens = tuple(part.casefold() for part in (*files, *globs, *directories))
    domain_terms = expand_domain_terms(query)
    identifiers = tuple(
        identifier
        for identifier in _dedupe([*domain_terms, *extract_identifiers(query)])
        if not any(identifier.casefold() in token for token in path_tokens)
    )[:12]
    quoted = extract_quoted_phrases(query)
    return QuerySignals(
        query=query,
        identifiers=identifiers,
        quoted=quoted,
        files=files,
        globs=globs,
        directories=directories,
        terms=_dedupe([*extract_terms(query), *domain_terms]),
        commit_sha=extract_sha(query),
        wants_history=_matches_any(folded, _HISTORY_KEYWORDS),
        wants_diff=_matches_any(folded, _DIFF_KEYWORDS),
        wants_callers=_matches_any(folded, _CALLERS_KEYWORDS),
        wants_callees=_matches_any(folded, _CALLEES_KEYWORDS),
        wants_references=_matches_any(folded, _REFERENCES_KEYWORDS),
        wants_definition=_matches_any(folded, _DEFINITION_KEYWORDS),
        wants_directory=_matches_any(folded, _DIRECTORY_KEYWORDS),
        wants_dependencies=_matches_any(folded, _DEPENDENCY_KEYWORDS),
        wants_dependents=_matches_any(folded, _DEPENDENTS_KEYWORDS),
        wants_blame=_matches_any(folded, _BLAME_KEYWORDS),
        wants_repository_list=_matches_any(folded, _REPOSITORY_KEYWORDS),
        wants_exports=_matches_any(folded, _EXPORT_KEYWORDS),
        wants_graph=_matches_any(folded, _GRAPH_KEYWORDS),
        wants_pull_request=_matches_any(folded, _PULL_REQUEST_KEYWORDS),
        wants_issue=_matches_any(folded, _ISSUE_KEYWORDS),
        github_number=(int(number.group(1)) if (number := _GITHUB_NUMBER.search(query)) else None),
        urls=tuple(dict.fromkeys(match.group(0) for match in _URL_PATTERN.finditer(query)))[:3],
    )


def strip_inline_citations(text: str) -> str:
    """Remove model-authored bracket citations so only verified markers remain."""
    return _INLINE_CITATION.sub("", text)


def one_line(text: str, *, limit: int) -> str:
    """Collapse whitespace and control characters into one bounded display line."""
    cleaned = _CONTROL.sub(" ", text)
    cleaned = strip_inline_citations(cleaned)
    collapsed = _WHITESPACE.sub(" ", cleaned).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(limit - 3, 0)].rstrip() + "..."


def truncate_text(text: str, *, limit: int) -> str:
    """Truncate display text on a word boundary without splitting a code fence."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    budget = limit - 3
    candidate = text[:budget]
    if "```" in text:
        fence_count = text.count("```")
        if fence_count % 2 == 1:
            last_fence = text.rfind("```")
            if last_fence < budget:
                candidate = text[:last_fence].rstrip()
    newline = candidate.rfind("\n")
    if newline > budget // 2:
        candidate = candidate[:newline]
    return candidate.rstrip() + "..."
