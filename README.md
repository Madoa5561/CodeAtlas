# CodeAtlas

CodeAtlasは、GitHubリポジトリを解析し、検証可能な根拠付き回答をDiscord Forumなどへ返すOSSのCode Intelligence Platformです。

> [!IMPORTANT]
> 現在は **0.1.4** として、M4「Agent + Model Provider」とM5「Discord Forum MVP」まで実装済みです。Qdrant、Hybrid Retrieval、Thrift parser、GitHub App/Webhook、Web API、MCPは未実装です。未実装機能を実装済みとしては扱いません。

## Status

| 項目 | 状態 |
| --- | --- |
| 要求分析、アーキテクチャ、Threat Model、ADR | 設計済み |
| Python package、設定、構造化logging、例外 | 実装済み |
| Plugin Registry、bootstrap、lifecycle | M1実装済み |
| pytest、Ruff、mypy、coverage、package build、CI/CD | 整備済み |
| Local Git登録、SHA固定tree/file/search/history | M2実装済み |
| SQLite transactional generation、TS/JS Tree-sitter索引 | M3実装済み |
| Agent Pipeline（Planner/Retriever/Ranker/Reasoner/Composer） | M4実装済み |
| Ollama Model Provider、canonical Tool Call、evidence citation | M4実装済み |
| DeepSeek（OpenAI互換）Model Provider | M4実装済み（Ollamaと排他） |
| 21のread-only Repository/Search Tool | M4実装済み |
| Discord Forum新規投稿・thread会話・冪等性・thread lock | M5実装済み |
| 会話履歴、retention purge、diagnostic CLI | M5実装済み |
| Qdrant、BM25/FTS、Vector Search、Hybrid Retrieval | 未実装（M6） |
| GitHub App/Webhook、PR/Issue Tool、Web API、MCP | 未実装（M6/M7） |
| Thrift parser、確定Call/Dependency Graph、図生成 | 未実装（M6/M7） |

詳細は[ロードマップ](docs/ROADMAP.md)を参照してください。

- M2 security boundary: [Repository Security](docs/REPOSITORY_SECURITY.md)
- M2 operations: [Repository Provider](docs/REPOSITORY_PROVIDER.md)
- M3 index: [Transactional Indexing](docs/INDEXING.md)
- M4 tool catalog: [Tools](docs/TOOLS.md)
- M4 agent: [ADR-0020](docs/adr/0020-agent-pipeline-implementation.md)
- M5 behavior: [Discord Behavior](docs/DISCORD_BEHAVIOR.md)

## Architecture

Ports and Adaptersを採用し、Domain/Application CoreをUI、GitHub、DB、LLMから分離します。AgentはPlanner、Retriever、Evidence Ranker、Reasoner、Response Composerの5段階に分かれ、Orchestratorはbudget、deadline、再計画、失敗分類だけを管理します。回答のcitationはTool evidenceから直接組み立て、LLMにSHAや行番号を創作させません。

- [アーキテクチャ](docs/ARCHITECTURE.md)
- [Knowledge Graph](docs/KNOWLEDGE_GRAPH.md)
- [Plugin/Event System](docs/PLUGINS_AND_EVENTS.md)
- [リスクとThreat Model](docs/RISKS.md)
- [ADR一覧](docs/adr/README.md)

## Quick Start

Python 3.12以上、Git、ripgrep（任意）が必要です。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]" -c constraints\py312.txt
python -m pytest
```

Linux/macOSではactivateだけが異なります。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]" -c constraints/py312.txt
python -m pytest
```

設定は`.env.example`を`.env`へコピーして変更します。`.env`はGit管理対象外です。

## Local Usage

```powershell
# 1. schemaを作成する（起動時に自動migrationはしない）
codeatlas-admin --json db upgrade

# 2. 解析対象repositoryをindexする
codeatlas-admin --json index full C:\work\linejs HEAD

# 3. 環境変数で対象repositoryとOllamaを設定する
$env:CODEATLAS_REPOSITORY__REGISTERED_PATHS='["C:/work/linejs"]'
$env:CODEATLAS_OLLAMA__ENABLED="true"
$env:CODEATLAS_OLLAMA__MODEL="ornith-1.5-9b:q4_k_m"

# 4. Discordを使わず同じcoreへ質問する
codeatlas-ask ask "Client クラスはどこで定義されていますか？"
codeatlas-ask --json ask "認証はどのファイルで処理されていますか？"
```

回答には結論、確認済みの事実、推測、不明点、repository、commit SHA、path、行番号、参照symbol、検索方法、確信度が含まれます。Ollamaが無効・停止・timeoutのときはdeterministic extractive modeで回答し、statusを`partial`にします。全ファイルをLLMへ渡すことはありません。

## Model Provider

`ollama`と`deepseek`のどちらか一方を有効化する（同時有効化は起動時に拒否）。

```powershell
# ローカルOllama
$env:CODEATLAS_OLLAMA__ENABLED="true"
$env:CODEATLAS_OLLAMA__MODEL="ornith-1.5-9b:q4_k_m"

# またはDeepSeek API
$env:CODEATLAS_OLLAMA__ENABLED="false"
$env:CODEATLAS_DEEPSEEK__ENABLED="true"
$env:CODEATLAS_DEEPSEEK__API_KEY="sk-..."
$env:CODEATLAS_DEEPSEEK__MODEL="deepseek-flash"
```

DeepSeekはOpenAI互換APIとして同じcanonical tool call・citation契約に正規化されます。API keyはlog・回答・imageへ出しません。設定の詳細は[CONFIGURATION.md](docs/CONFIGURATION.md)を参照してください。

## Discord

```powershell
$env:CODEATLAS_DISCORD__ENABLED="true"
$env:CODEATLAS_DISCORD__TOKEN="..."
$env:CODEATLAS_DISCORD__FORUM_CHANNEL_ID="123456789012345678"
codeatlas
```

- 新規Forum投稿は自動回答し、thread内の追加質問は`mention_or_reply`（既定）で回答します。
- 重複eventはSQLiteのidempotency ledgerで排除し、同一threadはconversation lockで直列化します。
- Bot自身の投稿には反応しません。詳細は[DISCORD_BEHAVIOR.md](docs/DISCORD_BEHAVIOR.md)を参照してください。

## Docker

```powershell
Copy-Item .env.example .env
docker compose build
docker compose up -d codeatlas
docker compose logs -f codeatlas
```

Ollamaはhostで起動し、containerから`host.docker.internal`経由で利用します。対象repositoryはread-only mountし、`CODEATLAS_REPOSITORY__REGISTERED_PATHS`へcontainer内pathを設定します。初回は`docker compose run --rm codeatlas codeatlas-admin --json db upgrade`とindex作成を明示実行してください。詳細は[デプロイ](docs/DEPLOYMENT.md)を参照してください。

## Quality Commands

```powershell
python -m pytest
python -m coverage run -m pytest
python -m coverage report
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
```

CIはLinux上でこれらを実行し、Docker image buildも検証します。`v*` tagのpushでGHCR imageとGitHub Release sdist/wheelが生成されます。詳細は[開発ガイド](docs/DEVELOPMENT.md)を参照してください。

## Security

- AIによるshell実行、repository書き込み、push、Issue/PR作成、secretの回答出力はありません。
- repository content、commit message、README、issue、外部HTMLはすべてuntrusted dataとして扱い、prompt injection境界を設けています。
- 任意HTTP取得、Qdrant、GitHub API、Webhookは0.1.0に存在しません。
- 秘密情報をIssueへ投稿しないでください。脆弱性はGitHubのPrivate vulnerability reportingから報告してください。詳細は[SECURITY.md](SECURITY.md)と[Security Design](docs/SECURITY_DESIGN.md)を参照してください。

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md)と[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)を確認してください。

## License

Apache License 2.0です。[LICENSE](LICENSE)を参照してください。
