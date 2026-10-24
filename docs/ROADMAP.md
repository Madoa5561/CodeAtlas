# 開発ロードマップ

## M0: 分析と初期化（完了）

- 要求分析、不足仕様、Threat Model、アーキテクチャ、Tool 契約、ADR
- Agent Pipeline、Unified Knowledge Graph、Plugin/Event/Interaction設計
- Python 3.12 パッケージ骨格と import test
- 現状と未実装を明示した README

完了条件: 文書整合、package import、標準テスト成功。

## M1: OSS/開発基盤（完了）

- License、Contributing、Code of Conduct、Security、Support、Changelog
- Pydantic Settings、structlog、例外 taxonomy
- pytest、Ruff、mypy、CI、Dockerfile/Compose、dependency lock
- Plugin Manifest/RegistryとComposition Rootの最小契約

完了条件: clean checkout から lint/type/test が再現可能。

## M2: Read-only Repository Slice（完了）

- Repository Port と Local Git Adapter
- root containment、安全な file/tree 読み取り
- ripgrep、Git 履歴、secret/binary/size exclusion
- built-in Repository Plugin、SHA固定snapshot、Python fallback

完了条件: fixture repository を SHA 固定で tree/read/search できる。

## M2.5: Extension Foundation（一部）

- Interaction Port、`CanonicalInteraction`/`CanonicalResponse`、capability matrix: M5で実装済み
- Plugin capability registryとlifecycle: 実装済み
- typed Event、transactional outbox、idempotent dispatcher: 未実装（M6）
- idempotencyとconversation lockはSQLite ledgerで実装済み

完了条件のうち「UI/plugin追加」と「同一threadでの冪等性」は満たす。event replay/outboxはM6で実装する。

## M3: Transactional Indexing（完了）

- SQLAlchemy/Alembic/SQLite schema
- TS/JS Tree-sitter symbol extraction
- generation、差分更新、削除、復旧、repository lock
- import/export/reference/extends/implements候補relation

完了条件: full/delta/retry 後の結果が同一で旧世代が破損しない。

FTS、Thrift、確定Call/Dependency Graph、Qdrant projectionはM3完了範囲に含めず、後続milestoneで実装する。

## M4: Agent and Ollama（完了）

- Model Port、canonical Tool Call、Ollama Adapter
- Planner/Retriever/Ranker/Reasoner/Composer、conversation memory
- 21のread-only Repository/Search Tool、SQLite index query
- prompt injection境界、citation検証、degraded mode

完了条件: fixture質問に正しいSHA/path/lineを含む回答を返す。→ linejs `Client`定義を`packages/linejs/client/client.ts:91`として回答することを実Ollamaで確認済み。

## M5: Discord MVP（完了）

- Forum event、follow-up、mention/reply policy
- idempotency、thread lock、typing、code-aware split、rate limit

完了条件: Docker Composeでテストrepositoryへの継続会話が成立する。→ 実装・offline contract test済み。実Discord環境での最終疎通はdeployment時のtoken設定で行う。

## M6: GitHub と Hybrid Retrieval

- GitHub App/Webhook、manual sync、PR/Issue Tool
- Qdrant、graph traversal、fusion/reranking、freshness benchmark

## M7: 拡張

- Thrift と TS の参照 graph
- 複数 Model Provider、Safe HTTP、CLI、Web API、MCP
- 要約、影響分析、PR review、Mermaid

## リリースゲート

- `0.1.0`（完了）: M5 と security test を満たす。
- public beta: M6、quota、データ削除、脆弱性報告手順を満たす。
- stable: compatibility、migration/backup、負荷/復旧試験を満たす。
