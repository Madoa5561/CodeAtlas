あなたはこのOSSプロジェクトの主任ソフトウェアアーキテクト兼実装担当です。

このリポジトリに配置されている `docs/REQUIREMENTS.txt` を最優先の要求仕様として読み、GitHubリポジトリを解析してDiscord Forum上で質問に回答する「GitHub Code Intelligence Platform」を開発してください。

## 最重要方針

このタスクで、いきなり全機能を一括実装しないでください。

最初に要求仕様を分析し、矛盾・不足・実装上のリスクを整理したうえで、拡張可能なアーキテクチャと実装計画を作成してください。その後、動作確認可能な最小構成から段階的に実装してください。

質問だけして作業を停止するのではなく、合理的な初期値を採用できる項目は判断して進めてください。重要な判断は設計文書に理由を記録してください。

## プロジェクトの目的

このOSSは、複数のGitHubリポジトリを対象として、以下を解析できるAIアシスタントです。

* ソースコード
* ディレクトリ構成
* クラス、関数、メソッド、型、インターフェース
* import、export、参照関係
* 呼び出し関係
* 依存関係
* Thriftのservice、method、struct、field、enum
* Git履歴
* commit、diff、branch、tag
* Pull Request
* Issue
* READMEおよびドキュメント
* 外部HTTPドキュメント

Discordの指定Forumチャンネルに新しい投稿が作成されたら自動回答し、そのスレッド内で継続的に会話できるようにします。

主要な対象リポジトリとして `evex-dev/linejs` を想定しますが、特定のリポジトリに密結合させず、複数リポジトリを登録可能にしてください。

## 必須アーキテクチャ

以下を疎結合なモジュールとして設計してください。

1. Discord Interface
2. Application Service
3. Agent Orchestrator
4. Planner
5. Tool Executor
6. Repository Provider
7. Repository Indexer
8. Lexical Search
9. Symbol Search
10. Vector Search
11. Knowledge Graph
12. Conversation Memory
13. Model Provider
14. Configuration
15. Observability
16. Webhook Receiver

Discord Botと解析コアを分離し、将来的に以下から同じコアを利用できる構造にしてください。

* Discord
* CLI
* Web API
* Web管理画面
* MCP Server
* VS Code拡張

## 技術要件

原則として以下を使用してください。

* Python 3.12以上
* discord.py
* FastAPI
* asyncio
* Pydantic v2
* SQLAlchemy 2
* Alembic
* SQLite
* Qdrant
* Tree-sitter
* GitPythonまたはGit CLI
* ripgrep
* pytest
* Ruff
* mypy
* Docker Compose
* structlog

ただし、より適切な選択肢がある場合は、理由をADRに記録したうえで変更して構いません。

## モデルプロバイダー

モデルプロバイダーを抽象化し、設定だけで次を切り替えられるようにしてください。

* Ollama
* OpenAI
* Anthropic
* Gemini
* DeepSeek
* OpenAI互換API

最初の動作対象はOllamaとし、モデル名やBase URLは環境変数で変更可能にしてください。

Tool Callingの形式がプロバイダーごとに異なるため、アプリケーション内部では共通のTool Call表現へ正規化してください。

## 検索方式

Vector Searchだけに依存しないでください。

以下を組み合わせたHybrid Retrievalを実装してください。

* ripgrepによる完全一致・文字列検索
* BM25等による字句検索
* Tree-sitterによるシンボル検索
* import・export・参照関係検索
* Call Graph検索
* Dependency Graph検索
* Vector Search
* Git履歴検索
* ファイルツリー検索

Plannerが質問内容から適切な検索方式を選び、必要に応じて複数回検索してください。

全ファイルを毎回LLMへ渡す設計は禁止します。

## GitHubおよびRepository Tool

最低限、以下のToolインターフェースを定義してください。

* list_repositories
* get_repository
* list_branches
* list_tags
* list_directory
* read_file
* search_text
* search_code
* find_symbol
* find_definition
* find_references
* find_callers
* find_callees
* get_dependencies
* get_dependents
* get_commit
* search_commits
* get_file_history
* get_blame
* compare_refs
* get_diff
* get_pull_request
* get_pull_request_diff
* list_issues

Toolの引数と戻り値はPydanticモデルで厳密に定義してください。

## HTTP Tool

HTTP Toolはセキュリティを最優先としてください。

* GETおよびHEADのみ
* localhost禁止
* private IP禁止
* link-local禁止
* metadata endpoint禁止
* リダイレクト先も検証
* タイムアウト設定
* 最大レスポンスサイズ設定
* 許可ドメイン設定
* Content-Type検証
* HTMLから本文抽出
* robots.txtと利用規約を考慮

SSRF対策をテストしてください。

## Tree-sitterとThrift

Tree-sitterでは最低限、以下を抽出してください。

* class
* function
* method
* interface
* type
* enum
* variable
* import
* export
* call expression
* inheritance
* implementation関係

TypeScriptとJavaScriptを最優先にしてください。

Thriftについては以下を解析できる専用パーサーまたはGrammar Adapterを設計してください。

* namespace
* include
* typedef
* const
* enum
* struct
* union
* exception
* service
* extends
* method
* arguments
* throws
* field ID
* required / optional

ThriftからTypeScript側への参照関係を可能な範囲でグラフ化してください。

## Knowledge Graph

最低限、次のノードを扱ってください。

* Repository
* Branch
* File
* Directory
* Symbol
* Class
* Function
* Method
* Interface
* Type
* Enum
* ThriftService
* ThriftMethod
* Commit
* PullRequest

最低限、次のエッジを扱ってください。

* CONTAINS
* DEFINES
* IMPORTS
* EXPORTS
* CALLS
* REFERENCES
* IMPLEMENTS
* EXTENDS
* DEPENDS_ON
* GENERATED_FROM
* MODIFIED_BY

初期実装ではSQLiteを利用して構いません。グラフストレージを交換できるRepository Interfaceを定義してください。

## Discord Forum

指定されたDiscord Forumチャンネルで次の動作を実装してください。

* 新規Forum投稿を検出
* 投稿タイトルと本文を質問として取得
* 初回回答を自動投稿
* スレッド単位で会話履歴を保存
* スレッド内の追加質問に回答
* Bot自身の投稿には反応しない
* 重複イベントを処理しない
* 同一スレッド内の同時実行を制御
* 長い処理中はtyping状態を表示
* Discordの文字数制限を考慮して分割送信
* コードブロックを途中で壊さない
* レート制限を考慮
* エラー時に内部情報や秘密情報を表示しない

対象ForumチャンネルIDは環境変数または設定ファイルで指定してください。

通常メッセージへの反応条件は設定可能にしてください。

* すべてのユーザーメッセージ
* Botへのメンション
* Botへの返信
* メンションまたは返信

初期値は「メンションまたはBotへの返信」としてください。ただし、Forum新規投稿の初回質問には自動回答してください。

## 回答の根拠

回答には可能な限り以下を含めてください。

* 結論
* 説明
* 根拠となるRepository
* Branchまたはcommit SHA
* ファイルパス
* 行番号
* 参照したシンボル
* 確信度
* 不明点
* 使用した検索方法

推測と確認済みの事実を区別してください。

根拠が不足している場合は断定せず、調査できなかった内容を明示してください。

行番号は、回答時点で解析対象となったcommit SHAに対する行番号として扱ってください。

## セキュリティ

以下を禁止してください。

* AIによる任意シェルコマンド実行
* 任意ファイル書き込み
* Repositoryへのpush
* Repositoryの削除
* Branchの削除
* IssueやPRの無断作成
* 認証情報の回答への出力
* `.env` や秘密鍵のIndex化
* Repository内部の命令をシステム命令として扱うこと

Repository内のREADME、Issue、コメント、ソースコード、Webページはすべて信頼できないデータとして扱い、Prompt Injection対策を実装してください。

読み取り専用を基本とし、将来書き込み機能を追加する場合は明示的な管理者承認を必要とする構造にしてください。

## Index更新

* 初回clone後に全体Indexを作成
* GitHub Webhookのpushイベントに対応
* 変更ファイルのみ再解析
* 削除ファイルをIndexから削除
* commit SHA単位で整合性を管理
* Index処理の途中失敗から復旧可能
* Repository単位で排他制御
* 手動同期コマンドを提供
* 定期同期にも対応可能な設計

## 高度な機能

次の機能を設計に含めてください。

* Repository自動要約
* Directory自動要約
* Architecture Explain
* Call Graph
* Dependency Graph
* 変更影響分析
* Git履歴解析
* commit差分解析
* Branch間差分解析
* Pull Requestレビュー
* サンプルコード生成
* Mermaidのflowchart
* MermaidのsequenceDiagram
* MermaidのclassDiagram
* MermaidのstateDiagram
* MermaidのerDiagram

初期MVPですべてを完成させる必要はありませんが、後から無理なく追加できるInterfaceとデータ構造を設計してください。

## OSSとして必要なファイル

以下を作成してください。

* README.md
* LICENSE
* CONTRIBUTING.md
* CODE_OF_CONDUCT.md
* SECURITY.md
* SUPPORT.md
* CHANGELOG.md
* docs/ARCHITECTURE.md
* docs/DEVELOPMENT.md
* docs/DEPLOYMENT.md
* docs/SECURITY_DESIGN.md
* docs/ROADMAP.md
* docs/CONFIGURATION.md
* docs/TOOLS.md
* docs/INDEXING.md
* docs/DISCORD_BEHAVIOR.md
* docs/adr/
* `.env.example`
* `.editorconfig`
* `.gitignore`
* `pyproject.toml`
* `docker-compose.yml`
* `Dockerfile`
* GitHub Actionsによるlint、type check、test

ライセンスはApache License 2.0を使用してください。

READMEには、現時点で未実装の機能を実装済みであるかのように書かないでください。

## 品質要件

* 型ヒントを付ける
* public APIにはdocstringを付ける
* domain層からDiscordやDBへ直接依存しない
* import循環を避ける
* 巨大な単一ファイルを作らない
* Provider固有ロジックをdomain層へ入れない
* エラーを握りつぶさない
* 構造化ログを使用
* 秘密情報をログへ出さない
* 外部I/Oにはタイムアウトを設定
* Retryは指数バックオフを使用
* pytestで重要処理をテスト
* 外部サービスはテストでmockする

## 今回の実行範囲

今回のタスクでは、以下を実施してください。

### フェーズ1：分析と設計

1. `docs/REQUIREMENTS.txt` を読む
2. 要求を機能要件・非機能要件へ整理
3. 不足や矛盾を洗い出す
4. Threat Modelを作成
5. アーキテクチャを設計
6. ADRを作成
7. データモデルを設計
8. Tool Interfaceを設計
9. 実装ロードマップを作成
10. MVPの完了条件を定義

### フェーズ2：プロジェクト基盤

1. Pythonプロジェクトを初期化
2. 推奨ディレクトリ構成を作成
3. 設定管理を実装
4. ロギングを実装
5. 基本的な例外体系を実装
6. CIを構築
7. Docker Composeを作成
8. テスト基盤を作成
9. OSS文書を作成

### フェーズ3：最小限の動作実装

1. Repository ProviderのInterface
2. ローカルGit Repository Provider
3. ファイルツリー取得
4. 安全なファイル読み取り
5. ripgrep検索
6. 基本的なTypeScript Tree-sitter解析
7. SQLiteへのIndex保存
8. Ollama Model Provider
9. 最小限のAgent Orchestrator
10. Discord Forumの新規投稿への回答
11. スレッド会話履歴
12. 根拠ファイルを伴う回答

フェーズ3の終了時点で、Docker Composeを使い、テスト用Repositoryを登録して、Discord Forum上で質問すると根拠付き回答が返る最小構成を動作させてください。

## 作業方法

作業開始前に以下を出力してください。

1. 要求仕様の理解
2. 採用するアーキテクチャ
3. 作成・変更予定ファイル
4. 実装順序
5. 主なリスク

その後は計画だけで停止せず、実装を開始してください。

作業中は次を守ってください。

* 小さく検証可能な単位で実装
* 各段階でテスト実行
* lintと型チェックを実行
* エラーが出たら原因を修正
* 未実装箇所をTODOとして明示
* モックだけで完成扱いにしない
* 既存ファイルを読む前に上書きしない
* 要求仕様を変更する場合は理由をADRへ残す

最後に以下を報告してください。

1. 実装した機能
2. 未実装の機能
3. テスト結果
4. 起動方法
5. 必要な環境変数
6. 既知の制約
7. 次に実装すべきタスク
8. セキュリティ上の注意点
