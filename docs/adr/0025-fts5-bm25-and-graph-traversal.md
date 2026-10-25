# ADR-0025: SQLite FTS5/BM25 Lexical ProjectionとGraph Traversal

- Status: Accepted
- Date: 2026-10-25

## Context

M4のretrievalはripgrep相当のliteral検索とsymbol indexに依存しており、identifierの完全一致や表記ゆれのない自然文検索に弱かった。M6はBM25 lexical retrievalとgraph traversalを要求する。SQLiteをsource of truthとし、追加サービスなしで検索できる必要がある。

## Decision

1. SQLite FTS5のline-level仮想テーブル`code_search_fts`を追加する。列は`content`（検索対象行）と`generation_id`/`path`/`line`（UNINDEXED provenance）とし、generation-scopedで不変世代に紐づける。
2. index生成時にsafe textの各行をFTSへ登録し、incremental reuse時は元generationのFTS行をコピーする。failed generationのFTS行は削除する。既存DBはmigration `0003_m6_fts`で`file_versions.content_utf8`からbackfillする。
3. queryはuntrusted文字列をtoken化・quoteしたbounded MATCH式に変換し、`bm25()`昇順でrankingする。path prefixで絞り込み、`RetrievalLimits`で上限を強制する。日本語はunicode61の制約上、literal/略語検索と併用する。
4. FTSはprojectionでありsource of truthではない。不整合時は再indexで再構築できる。`SqliteIndexQuery`は`query_only=ON`のままFTSを読む。
5. `traverse_graph` Toolはsymbolまたはfileを起点に、既存のrelation candidateをBFSで深さ1-3、edge kind、incoming/outgoing/both、結果上限付きで辿る。call/dependency graphはparser候補でありconfidenceとprovenanceを付与する。確定resolverではない。
6. Plannerは通常質問で`search_lexical`とliteral/symbol検索を併用し、graph intent（call graph/dependency graph等）では`traverse_graph`を計画する。

## Consequences

- 追加サービスなしでBM25検索とgraph探索が使える。FTS5が利用できないSQLite buildではmigrationが失敗し、起動前に検出できる。
- FTSは行単位のため行番号が正確だが、行をまたぐ構文やCJK分かち書きには限界がある。Qdrant/vector検索と統合rankingは後続で実装する。
- graph traversalは未解決のraw targetをnode名として扱う箇所があり、確定Call GraphはM7のresolverで強化する。
