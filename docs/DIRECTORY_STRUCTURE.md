# ディレクトリ構成

現在は初期骨格のみを作り、以下を目標構成とする。空ディレクトリや未実装 module は先に作らない。

```text
.
├── docs/
│   ├── adr/
│   ├── ARCHITECTURE.md
│   ├── REQUIREMENT_ANALYSIS.md
│   ├── OPEN_QUESTIONS.md
│   ├── RISKS.md
│   ├── DIRECTORY_STRUCTURE.md
│   ├── TOOL_INTERFACES.md
│   └── ROADMAP.md
├── src/codeatlas/
│   ├── domain/{models,ports}/
│   ├── application/agent/{planner,retriever,ranker,reasoner,composer}/
│   ├── application/{indexing,retrieval,events}/
│   ├── plugin_api/{manifest,capabilities,lifecycle}/
│   ├── adapters/inbound/{discord,http,cli,mcp,vscode}/
│   ├── adapters/outbound/{git,github,models,persistence,search}/
│   ├── infrastructure/{plugins,event_bus}/
│   ├── entrypoints/{api,bot,worker}/
│   └── bootstrap.py
├── tests/{unit,contract,integration,security}/
├── pyproject.toml
└── README.md
```

## 依存ルール

- `domain` は adapters/entrypoints を参照しない。
- `application` は具象 Provider を参照せず domain port を受け取る。
- adapters 同士を直接接続せず composition root で配線する。
- Provider 固有 DTO は adapter 内で canonical model へ変換する。
- UI固有のmessage/thread/format型はInteraction Adapterの外へ出さない。
- Pluginは`plugin_api`だけに依存し、内部application実装をimportしない。
- Event handlerはDomainを直接変更せずApplication commandを呼び出す。
- 巨大な `utils.py` は作らない。
