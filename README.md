# CodeAtlas

CodeAtlasは、GitHubリポジトリを解析し、検証可能な根拠付き回答をDiscord Forumなどへ返すことを目指すOSSのCode Intelligence Platformです。

> [!IMPORTANT]
> 現在はM1「OSS/開発基盤」まで実装済みです。Repository解析、Discord Bot、LLM接続、Tree-sitter、Qdrant接続は未実装です。

## Status

| 項目 | 状態 |
| --- | --- |
| 要求分析、アーキテクチャ、Threat Model、ADR | 設計済み |
| Python package、設定、構造化logging、例外 | 実装済み |
| Plugin Registry、bootstrap、lifecycle | M1最小契約を実装済み |
| pytest、Ruff、mypy、coverage、package build、CI | 整備済み |
| Docker foundation process、ComposeのQdrant定義 | 整備済み、Qdrant接続は未実装 |
| Local Git登録、SHA固定tree/file/search/history | M2実装済み |
| SQL索引、Tree-sitter、Qdrant、Hybrid Retrieval | 未実装 |
| Agent Pipeline、Model Provider、Discord/API/CLI | 未実装 |

詳細は[ロードマップ](docs/ROADMAP.md)を参照してください。

M2 Repository Providerのsecurity boundaryは[Repository Security](docs/REPOSITORY_SECURITY.md)、操作仕様は[Repository Provider](docs/REPOSITORY_PROVIDER.md)を参照してください。CLIはまだ未実装です。

## Architecture

Ports and Adaptersを採用し、Domain/Application CoreをUI、GitHub、DB、LLMから分離します。将来のAgentはPlanner、Retriever、Evidence Ranker、Reasoner、Response Composerに分かれます。

- [アーキテクチャ](docs/ARCHITECTURE.md)
- [Knowledge Graph](docs/KNOWLEDGE_GRAPH.md)
- [Plugin/Event System](docs/PLUGINS_AND_EVENTS.md)
- [リスクとThreat Model](docs/RISKS.md)
- [ADR一覧](docs/adr/README.md)

## Quick Start

Python 3.12以上が必要です。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
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

設定例は`.env.example`にあります。`.env`はGit管理対象外です。現在のservice processは外部接続を行わず、停止signalを待つfoundation lifecycleのみです。

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

詳細は[開発ガイド](docs/DEVELOPMENT.md)を参照してください。

## Docker

```powershell
docker compose up --build -d codeatlas
docker compose --profile qdrant up --build -d
```

Qdrantを起動しても、M1のCodeAtlasはまだ接続しません。詳細は[デプロイ](docs/DEPLOYMENT.md)を参照してください。

## Security

任意shell実行、repository書き込み、外部service接続はM1にありません。秘密情報をIssueへ投稿しないでください。脆弱性はGitHubのPrivate vulnerability reportingから報告してください。詳細は[SECURITY.md](SECURITY.md)を参照してください。

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md)と[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)を確認してください。

## License

Apache License 2.0です。[LICENSE](LICENSE)を参照してください。
