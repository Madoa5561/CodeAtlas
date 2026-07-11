<<<<<<< HEAD
# CodeAtlas

GitHub リポジトリを解析し、根拠付きの回答を Discord Forum などへ返すことを目指す OSS の Code Intelligence Platform です。

> [!IMPORTANT]
> 現在は設計および Python プロジェクトの初期化段階です。Discord Bot、リポジトリ解析、検索、LLM 接続は未実装です。

## Vision

- 複数リポジトリのコード、シンボル、依存関係、Git 履歴を横断して調査する
- 回答に commit SHA、ファイル、行番号、検索方法、確信度を添える
- Discord に依存しない解析コアを CLI、Web API、MCP Server などから再利用する
- リポジトリ内の内容を信頼できない入力として扱い、読み取り専用を既定にする

## Status

| 項目 | 状態 |
| --- | --- |
| 要求分析、アーキテクチャ、Threat Model | 設計済み |
| Python パッケージ骨格 | 初期化済み |
| Repository Provider、索引、Hybrid Retrieval | 未実装 |
| Agent Pipeline、Knowledge Graph、Plugin/Event System | 設計済み、未実装 |
| Model Provider、Agent Pipeline | 未実装 |
| Discord / API / CLI | 未実装 |

詳細は [ロードマップ](docs/ROADMAP.md) を参照してください。

## Architecture

Ports and Adapters を採用し、Domain/Application Core を UI、GitHub、DB、LLM から分離します。Agent は Planner、Retriever、Evidence Ranker、Reasoner、Response Composer に分かれ、TypeScript、Thrift、Git History、Call Graph、Dependency Graphを横断する Knowledge Graph を利用します。

- [要求分析](docs/REQUIREMENT_ANALYSIS.md)
- [アーキテクチャ](docs/ARCHITECTURE.md)
- [不足仕様](docs/OPEN_QUESTIONS.md)
- [リスクと Threat Model](docs/RISKS.md)
- [Knowledge Graph](docs/KNOWLEDGE_GRAPH.md)
- [Plugin/Event System](docs/PLUGINS_AND_EVENTS.md)
- [設計レビュー](docs/DESIGN_REVIEW.md)
- [ADR 一覧](docs/adr/README.md)

## Development

前提は Python 3.12 以上です。現段階では実行時の外部依存関係はありません。

```powershell
python -m pip install -e .
python -m unittest discover -s tests
```

正式な開発用依存関係と Ruff、mypy、pytest の導入は基盤マイルストーンで行います。

## Security

任意コマンド実行、任意ファイル書き込み、リポジトリへの変更を Tool として公開しません。正式な `SECURITY.md` はプロジェクト基盤フェーズで追加予定です。公開前は Issue に秘密情報を投稿しないでください。

## Contributing

コントリビューション手順、行動規範、ライセンスファイルはプロジェクト基盤フェーズで整備予定です。現段階では設計レビューを優先します。

## License

Apache License 2.0 を採用予定です。正式な `LICENSE` ファイルが追加されるまで配布可能なリリースではありません。
=======
# linejs-code-assistant
>>>>>>> 7a485a2ec5d817e27efb055a9d4e2f2751ff61c9
