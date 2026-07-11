# Plugin System と Event System

## Plugin System

### 拡張ポイント

- Interaction: Discord、CLI、Web API、MCP、VS Code
- Repository Provider: local Git、GitHub、将来のGitLab
- Parser: TypeScript、JavaScript、Thrift、追加言語
- Retriever/Ranker: lexical、symbol、graph、vector、ranking strategy
- Model Provider: Ollama、OpenAI、Anthropic等
- Storage/Projection: SQLite、Qdrant、将来のgraph store
- Event Handler: metrics、index projection、notification

### ManifestとCapability

各pluginは`plugin_id`、semantic version、CodeAtlas API range、capability IDs、config schema、priorityを宣言する。Registryは起動時にentry pointから候補を列挙し、allowlist、互換性、重複、設定を検証してからactivateする。

Capabilityは`kind:name:version`で識別し、Applicationはplugin classではなくtyped capability portを解決する。競合は明示設定を優先し、暗黙のfirst-winsを禁止する。

### Lifecycle

`discover -> validate -> configure -> start -> ready -> stop`を定義する。start失敗時は逆順にstopし、必須capability不足ならfail fast、任意capabilityならdegraded modeとする。hot reloadは初期範囲外とする。

### Security

Pluginは信頼済みPythonコードでありsandboxではない。packageの自動download、repositoryからの自動load、任意path importを禁止する。Tool capabilityは既存authorization policyを迂回できない。

## Event System

### Event分類

- Domain Event: `RepositoryRegistered`, `SnapshotActivated`, `ConversationUpdated`
- Application Event: `InteractionReceived`, `InvestigationCompleted`, `ResponseComposed`
- Integration Event: `IndexRequested`, `ResponsePublishRequested`, `ProjectionRebuildRequested`

### Envelope

`event_id`、`event_type`、schema version、occurred_at、correlation_id、causation_id、aggregate ID/version、payload、metadataを持つ。秘密やrepository本文をpayloadへ入れない。

### Delivery Semantics

Domain stateとoutboxを同一SQLite transactionで保存する。Dispatcherはat-least-onceで配送し、consumerは`event_id`で冪等化する。順序はrepositoryまたはconversation aggregate内のみ保証し、retryはbounded exponential backoff、最終失敗はdead-letterへ移す。

### Event Bus Port

- `publish(events)`: transaction内outbox追加
- `subscribe(event_type, handler)`: composition時の静的登録
- `dispatch(batch_size, deadline)`: worker処理
- `replay(filter, dry_run)`:管理者操作、既定無効

Event handlerは別aggregateを同期更新せずcommandを発行する。これにより循環eventと長いtransactionを避ける。

## UI追加手順

1. Interaction Pluginとしてmanifestとcapabilityを宣言する。
2. 外部入力を`CanonicalInteraction`へ変換する。
3. `CanonicalResponse`をUIの表現へ変換する。
4. idempotency、認証、rate limit、capability matrixのcontract testを実装する。
5. application/domainを変更せずcomposition設定で有効化する。
