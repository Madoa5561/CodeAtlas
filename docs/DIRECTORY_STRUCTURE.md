# ディレクトリ構成

0.1.0時点の実装構成を示す。未実装moduleの空directoryは作らない。

```text
.
├── docs/
│   ├── adr/
│   ├── ARCHITECTURE.md
│   ├── CONFIGURATION.md
│   ├── DATABASE.md
│   ├── DEPLOYMENT.md
│   ├── DEVELOPMENT.md
│   ├── DISCORD_BEHAVIOR.md
│   ├── INDEXING.md
│   ├── KNOWLEDGE_GRAPH.md
│   ├── PLUGINS_AND_EVENTS.md
│   ├── REPOSITORY_PROVIDER.md
│   ├── REPOSITORY_SECURITY.md
│   ├── REQUIREMENTS.md
│   ├── RISKS.md
│   ├── ROADMAP.md
│   ├── SECURITY_DESIGN.md
│   ├── TOOLS.md
│   └── TOOL_INTERFACES.md
├── src/codeatlas/
│   ├── domain/                 # stdlibのみ: models, ports, errors
│   ├── application/
│   │   ├── agent/              # Planner, Retriever, Ranker, Reasoner, Composer, Orchestrator
│   │   ├── tools/              # Pydantic tool schemas, catalog, executor, tools
│   │   ├── interactions.py     # idempotency + conversation lock + agent use case
│   │   ├── indexing.py         # M3 transactional indexing
│   │   └── repositories.py
│   ├── plugin_api/             # manifest, capability, lifecycle, registry
│   ├── adapters/
│   │   ├── discord/            # M5 interaction adapter, presentation, plugin
│   │   ├── model/              # M4 Ollama provider + plugin
│   │   ├── parsing/            # Tree-sitter parser + plugin
│   │   └── repository/         # local Git provider, security, registry, plugin
│   ├── infrastructure/
│   │   ├── conversation/       # M5 SQLite conversation store/models
│   │   ├── indexing/           # M3 schema, store, query, migrations
│   │   ├── health.py
│   │   └── logging.py
│   ├── migrations/             # packaged Alembic environment/versions
│   ├── entrypoints/            # service, admin CLI, diagnostic CLI, healthcheck
│   ├── runtime.py              # composition root (唯一adapterを組み立てる)
│   └── bootstrap.py
├── migrations/                 # root Alembic entry point (CI/local tooling)
├── constraints/py312.txt
├── tests/                      # unit/contract/integration/securityをfile名で区別
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## 依存ルール

- `domain` は stdlib と domain 自身だけを参照する。
- `application` は domain port と application 内 module だけを参照し、具象 adapter を参照しない。例外として `application/tools` の Pydantic input model は ADR-0020 の明示境界とする。
- adapter 同士を直接接続せず、`runtime.py` の composition root で配線する。
- Provider 固有 DTO は adapter 内で canonical model へ変換する。
- UI 固有の message/thread/format 型は Interaction Adapter の外へ出さない。
- Plugin は `plugin_api` にだけ依存し、内部 application 実装を import しない。
- Event handler は Domain を直接変更せず Application command を呼び出す（M6）。
- 巨大な `utils.py` は作らない。
