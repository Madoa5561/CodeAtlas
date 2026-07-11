# 設計レビュー

## Findings

### High: Agent Orchestratorへの責務集中

計画、検索、推論、根拠評価、文章生成の境界がなく、モデル変更や評価をコンポーネント単位で行えなかった。5段階のtyped pipelineへ分離し、Orchestratorを制御責務だけに限定した。

### High: Knowledge Graphの横断意味論不足

汎用`GraphNode`/`GraphEdge`だけでは、Thrift serviceからgenerated TypeScript、symbolからcommit、callからpackage dependencyを辿る規則と同一性が未定義だった。snapshot-scoped identity、provenance、cross-domain edge、projectionを定義した。

### Medium: UI Adapterの非対称性

Discordが主要フローに直接現れ、他UIのthread、streaming、長さ制限の差を吸収する契約がなかった。Interaction Portとcapability-driven presentationへ置き換えた。

### Medium: 拡張点とロード順序が未定義

ProviderやParserを追加できる設計意図はあったが、互換性、設定schema、lifecycle、競合解決が未定義だった。Plugin Manifest、Capability、Registry、互換性検証を追加した。

### Medium: 非同期副作用と監査の不足

Webhookの冪等性はあったが、索引完了後の派生処理、UI通知、plugin handlerを安全に配送するevent契約がなかった。Domain Eventとtransactional outboxを採用した。

## Residual Risks

- Plugin sandboxはPython process内では完全なsecurity boundaryにならない。第三者pluginは信頼済みコードとして管理する。
- Graphのcross-language linkはheuristicを含むためconfidenceとprovenanceが必須である。
- Rankerのscoreは評価datasetなしでは校正できない。
- Event schema evolution、replay、dead-letterの運用詳細は実装ADRで確定が必要である。
