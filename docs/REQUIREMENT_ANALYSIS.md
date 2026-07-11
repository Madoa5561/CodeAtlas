# 要求分析

## 仕様の優先順位

1. 実在する `docs/REQUIREMENTS.md` を正本とする。
2. 要求内の `docs/REQUIREMENTS.txt` はファイル名の誤記と判断する。
3. 本文内で範囲が競合する場合、今回の指示である「設計文書と最小限の Python 初期構成」を優先し、フェーズ3は実装しない。

## 目的と境界

複数の GitHub リポジトリを読み取り専用で解析し、質問に検証可能な根拠を伴う回答を生成する。最初のチャネルは Discord Forum、最初のモデルは Ollama、主要解析対象は TypeScript/JavaScript と Thrift である。ただし解析コアは特定チャネル、モデル、リポジトリに依存させない。

## 機能要件

| ID | 要求 | MVP |
| --- | --- | --- |
| FR-01 | 複数リポジトリの登録と参照 | 必須 |
| FR-02 | branch/tag/tree/file/Git 履歴の読み取り | 必須範囲を限定 |
| FR-03 | 完全一致、字句、シンボル、グラフ、ベクトル、Git の Hybrid Retrieval | 字句・基本シンボルを先行 |
| FR-04 | TypeScript/JavaScript と Thrift の構造解析 | TS/JS 基本構文を先行 |
| FR-05 | commit SHA 単位の索引、差分更新、削除反映、復旧 | 必須 |
| FR-06 | Provider 非依存の Tool Call 正規化 | Ollama で実証 |
| FR-07 | Discord Forum 新規投稿とスレッド会話 | 必須 |
| FR-08 | 根拠、確信度、不明点、検索方法を含む回答 | 必須 |
| FR-09 | 安全な外部 HTTP 文書取得 | MVP 後 |
| FR-10 | Webhook、手動同期、将来の定期同期 | Webhook と手動を先行 |
| FR-11 | 要約、図、影響分析、PR レビュー | MVP 後 |
| FR-12 | CLI、Web API、管理画面、MCP、VS Code からコア再利用 | Port を先行設計 |

## 非機能要件

| ID | 要求 | 設計上の扱い |
| --- | --- | --- |
| NFR-01 | 読み取り専用、安全な HTTP、秘密情報保護 | deny-by-default と境界検証 |
| NFR-02 | 非同期 I/O、timeout、指数 backoff | Adapter 共通ポリシー |
| NFR-03 | 型、docstring、非循環、疎結合 | Ports and Adapters と CI |
| NFR-04 | 構造化ログ、秘密情報非出力 | redaction 付き observability port |
| NFR-05 | 重複排除、排他、障害復旧、SHA 整合性 | 永続 ledger と transaction |
| NFR-06 | 重要処理のテスト、外部サービスの mock | unit/contract/integration を分離 |
| NFR-07 | OSS の導入、運用、貢献、脆弱性報告が明確 | 基盤フェーズで文書を完成 |

## 制約

- Python 3.12 以上、asyncio、Pydantic v2、SQLAlchemy 2 を中心とする。
- SQLite をメタデータ/グラフの初期ストア、Qdrant をベクトルストアとする。
- LLM に全ファイルを毎回送信しない。
- Repository、Issue、PR、外部ページはすべて untrusted data とする。

## MVP 完了条件

1. Docker Compose で必要サービスを起動できる。
2. テスト用 repository を登録し、特定 commit SHA の索引を再現可能に作れる。
3. ファイルツリー、安全な読み取り、ripgrep、基本 TS/JS シンボル検索が動く。
4. Ollama と共通 Tool Call 契約を介して最小 Orchestrator が動く。
5. Discord Forum の新規投稿と追加質問に、重複なく順序制御して回答する。
6. 回答に repository、SHA、path、line、検索方法、不明点を含める。
7. 秘密ファイル除外、Prompt Injection 境界、主要な失敗/復旧をテストする。
8. Ruff、mypy、pytest、主要 integration test が CI で成功する。
