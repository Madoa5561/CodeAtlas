# Changelog

このプロジェクトは[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)の形式とSemantic Versioningを採用します。

## [Unreleased]

### Planned

- M6: Qdrant、BM25/FTS、Hybrid Retrieval、GitHub App/Webhook、PR/Issue Tool、Web API
- M7: Thrift parser、確定Call/Dependency Graph、複数Model Provider、MCP、VS Code拡張

## [0.2.1] - 2026-10-25

### Added

- M6 Web API: FastAPI `/healthz`、`/repositories`、`/ask`（repository scope対応）と組み込みuvicorn lifecycle
- M6 GitHub Webhook receiver: `X-Hub-Signature-256` HMAC検証、payload size上限、`X-GitHub-Delivery` によるSQLite冪等ledger
- migration `0004_m6_webhook` と `webhook_deliveries` テーブル
- API/Webhook設定（`CODEATLAS_API__*`）とドキュメント、ADR-0026

### Changed

- `InteractionApplicationService` がrepository scopeを受け取れるようになり、HTTP APIから単一repositoryへ質問を限定できる

## [0.2.0] - 2026-10-25

### Added

- M6 lexical retrieval: SQLite FTS5 line-level index（migration `0003_m6_fts`）、BM25 ranking、`search_lexical` Tool、incremental reuse時のFTSコピー、失敗generationのFTS削除、既存indexのbackfill
- M6 graph traversal: `traverse_graph` Tool（symbol/file起点、edge種別、incoming/outgoing/both、深さ1-3、上限付き）
- Plannerへlexical hybrid検索とgraph intentを追加

### Changed

- index schema headを`0003_m6_fts`へ更新。service起動前に`codeatlas-admin db upgrade`が必要
- lexical検索はliteral検索と併用し、Rankerのmethod diversityで統合する

## [0.1.5] - 2026-10-25

### Added

- `find_exports` Tool: export宣言、export relation、`package.json`/`deno.json` の `exports` マップ、entrypointの再exportを照合し、公開APIとしてのexportを確認
- Plannerにexport/エクスポート/公開/entrypoint intentを追加

### Changed

- Discord回答をcomposerのblock列からの単一描画に統一し、citation・confidence・不明点・検索方法の二重フッターを解消
- 引用を本文で実際に使ったevidence中心に絞り、最大8件・根拠一覧はpath/行/symbol/検索方法のコンパクト形式に変更
- 回答全体のbudgetを単一message長からagent budgetへ変更し、長い根拠付き回答はDiscord側で複数messageへ分割

## [0.1.4] - 2026-10-25

### Fixed

- 起動失敗時のlogが`service_failed`だけで原因不明だった問題を修正し、validation errorはfield locationのみ、CodeAtlasErrorはsafe error code、その他は例外型名だけを出力するようにした（入力値・secretは決して出力しない）

## [0.1.3] - 2026-10-25

### Added

- DeepSeek Model Provider（OpenAI互換 `/chat/completions`）: canonical tool call、JSON output、thinking制御、bounded retry、`/models` probe、degraded mode
- `CODEATLAS_DEEPSEEK__*` 設定と`model:deepseek` built-in plugin
- DeepSeekとOllamaの同時有効化を拒否する起動時validation
- provider選択のcomposition root対応とMockTransportによるcontract test（live testは`CODEATLAS_TEST_DEEPSEEK=1`で任意実行）
- ADR-0023と設定・デプロイ・READMEのDeepSeek手順

### Security

- DeepSeek API keyを`SecretStr`で保持し、401/403・timeout・不正応答のerror/repr/logへkey・prompt・response bodyを含めない
- credentialed/query付きbase URLとproxy環境変数を拒否/無視し、redirectを追跡しない

## [0.1.2] - 2026-10-25

### Added

- Plannerに日本語・英語の技術用語辞書（認証、設定、依存、ログイン、エラー等）を追加し、識別子のない日本語質問でも`auth`や`config`などソース上の識別子へ決定的に展開するようにした
- 用語展開のunit testとplanner testを追加

### Fixed

- 日本語の自然文質問が原文のliteral検索だけになり「根拠が見つからない」と回答していた問題を改善した

## [0.1.1] - 2026-10-25

### Fixed

- Composeのrelease image tagをGHCRで公開されるtagに合わせ、本番では`vX.Y.Z`へ固定する手順を明記した
- Release workflowが`vX.Y.Z`に加えて`X.Y.Z`のcontainer tagも公開するよう修正した

## [0.1.0] - 2026-10-25

### Added

- M5 Discord Forum adapter: 新規投稿の自動回答、thread継続会話、mention/reply policy、typing、code fenceを壊さない分割送信、rate limit委譲、安全なエラー表示
- M5 durable conversation memory: SQLite `conversations`/`conversation_messages`/`interaction_records`/`conversation_locks`、atomic idempotency claim、crash recovery、thread lock、retention purge
- M4 five-stage Agent Pipeline: deterministic Planner、Tool Retriever、Evidence Ranker、grounded Reasoner、Citation Composer、budget/deadline Orchestrator
- M4 Ollama Model Provider: canonical tool call正規化、JSON mode、thinking control、bounded retry、degraded mode、prompt/secret非漏洩
- M4 read-only Tool群 21個: repository、tree/file、text/code search、symbol/definition/reference、callers/callees、dependencies、commit/history/blame/diff
- M4 SQLite index query adapter: symbol、relation、textのbounded read-only query
- M4 citation付き回答: repository、commit SHA、path、行番号、symbol、検索方法、確信度、不明点を明示
- M4 prompt injection境界: repository contentをuntrusted envelopeとして分離し、modelのcitationをevidenceと照合
- M5 diagnostic CLI `codeatlas-ask`（Discordなしで同じcoreを利用）
- M5 `codeatlas-admin conversations purge`による保持期間ベースの削除
- M4/M5 ADR-0020、ADR-0021、ADR-0022とTools/Discord Behavior/Deployment文書
- GitHub Actionsのcontainer build検証、tag起点のGHCR image publishとGitHub Release成果物添付
- M3 SQLAlchemy/Alembic/SQLite schemaと明示migration CLI
- immutable staging generation、atomic active切替、rollback、lease/stale recovery
- safe UTF-8本文、file version、symbol、import/export/reference relation永続化
- built-in TypeScript/JavaScript Tree-sitter Parser Plugin
- full/incremental indexingとparser/policy fingerprintによるunchanged reuse
- M3 indexing/database/parser/recovery文書とADR-0015からADR-0019
- M2 local Git Repository Port、Application Service、in-memory Registry、built-in Plugin
- SHA固定tree/file/search/commit/history/blame/diff操作
- bounded read-only Git CLI runnerとripgrep/Python snapshot search
- repository path、binary、basic credential、resource limit security policy
- Apache License 2.0とOSS governance文書
- Python 3.12 package metadata、runtime/development依存の分離、constraints
- Pydantic Settingsによる検証済み設定
- structlogによるJSON/console loggingとsecret redaction
- 安全な共通例外体系
- Plugin Manifest、Capability、Registry、lifecycle rollback
- Composition Rootとgraceful shutdown
- pytest、Ruff、mypy、coverage、package buildのGitHub Actions
- 非root Docker imageとQdrantを任意profileにしたDocker Compose

### Changed

- Agent回答のcitationはLLMではなくTool evidenceから生成する方式にした
- Discord pluginは`CODEATLAS_DISCORD__ENABLED=false`でcredentialなしに起動できる
- Composeをread-only root filesystem、tmpfs、host Ollama接続、release image指定へ更新した

### Security

- Toolのstrict argument validation、tool allowlist、repository scope authorization、timeout、payload上限
- prompt injection耐性test、invented citationの破棄、model failure時の安全なfallback
- Discordの重複event排除、thread排他、busy応答のsafe message化、message content/tokenの非log
- Ollama/HTTPのcredentialed URL拒否、proxy環境変数の無視、provider errorのsanitize
- staging失敗・cancel時の旧active generation維持
- unsafe/secret/binary contentのSQLite非保存とactivation前validation
- repository単位のprocess内lockとSQLite lease
- secret model representationとstructured logのredaction test
- plugin allowlist、API compatibility、capability conflict validation
