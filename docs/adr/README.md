# ADR 一覧

| ADR | 状態 | 決定 |
| --- | --- | --- |
| [0001](0001-modular-monolith-ports-and-adapters.md) | Accepted | Modular Monolith と Ports and Adapters |
| [0002](0002-immutable-snapshot-index.md) | Accepted | commit SHA と generation による索引公開 |
| [0003](0003-provider-neutral-tool-calls.md) | Accepted | Provider 非依存 Tool Call |
| [0004](0004-hybrid-retrieval.md) | Accepted | Hybrid Retrieval |
| [0005](0005-read-only-security-boundary.md) | Accepted | deny-by-default の読み取り専用境界 |
| 0006 | Proposed | SQLite FTS5 と BM25 ライブラリ |
| [0007](0007-git-cli.md) | Accepted | 固定read-only Git CLI adapter |
| 0008 | Proposed | Tree-sitter/Thrift grammar |
| [0009](0009-python-dependency-management.md) | Accepted | venv、pip、constraintsによる依存管理 |
| 0010 | Proposed | job queue と process coordination |
| [0011](0011-agent-pipeline.md) | Accepted | Agentを5つの責務へ分離 |
| [0012](0012-unified-knowledge-graph.md) | Accepted | 横断Unified Knowledge Graph |
| [0013](0013-plugin-event-interaction.md) | Accepted | Plugin、Event、UI非依存Interaction |
| [0014](0014-sha-pinned-search-snapshot.md) | Accepted | SHA固定temporary search snapshot |
| [0015](0015-sqlalchemy-alembic-sqlite.md) | Accepted | SQLAlchemy、Alembic、SQLite |
| [0016](0016-transactional-index-generations.md) | Accepted | immutable staging generationとatomic publication |
| [0017](0017-tree-sitter-grammar-versions.md) | Accepted | Tree-sitter grammar version管理 |
| [0018](0018-incremental-file-identity.md) | Accepted | incremental file/symbol identity |
| [0019](0019-sqlite-source-of-truth.md) | Accepted | SQLiteをM3 source of truthとする方針 |
| [0020](0020-agent-pipeline-implementation.md) | Accepted | M4 Agent Pipelineの実装方式 |
| [0021](0021-discord-interaction-and-conversation.md) | Accepted | M5 Discord Adapterと会話状態の永続化 |
| [0022](0022-release-and-deployment-0-1-0.md) | Accepted | 0.1.0の配布・デプロイ方式 |
| [0023](0023-deepseek-openai-compatible-provider.md) | Accepted | DeepSeekをOpenAI互換Providerとして追加 |
| [0024](0024-discord-rendering-and-export-verification.md) | Accepted | Discord回答の単一描画とexport確認 |
| [0025](0025-fts5-bm25-and-graph-traversal.md) | Accepted | SQLite FTS5/BM25 Lexical ProjectionとGraph Traversal |
