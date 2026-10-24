# Security Design

## M1 Security Boundary

M1は外部network、repository、Discord、LLM、Qdrantへ接続しない。主な対象は設定、logging、plugin登録、process/container、CI supply chainである。将来の脅威全体は[RISKS.md](RISKS.md)を参照する。

## Configuration and Secrets

- secret fieldはPydantic `SecretStr`で表現しreprをmaskする。
- integrationが無効ならsecretを要求せず、有効ならfail fastする。
- `.env`は`.gitignore`と`.dockerignore`の対象である。
- repository write設定はM1で常に拒否する。
- resource設定は上限・下限を持つ。

## Logging

structlogと標準loggingを`ProcessorFormatter`で統合する。timestamp、level、logger、eventを出力し、contextvarsでrequest/correlation IDを追加できる。

key名にtoken、password、secret、authorization、api_keyを含む値、credential形式の文字列、`SecretStr`は、list/tupleを含むnested構造でも`[REDACTED]`へ置換する。Repository本文や会話全文をlog APIへ渡さないことが第一防御であり、redactionだけに依存しない。

redactionはkey-basedであり、自由文message内のsecretを検出するDLPではない。secretをmessageへ連結してはならない。

## Exceptions

`CodeAtlasError.safe_message`だけをUIへ公開し、`internal_cause`は内部診断用に分離する。reprとstrにcauseやstack traceを含めない。組み込み`TimeoutError`、Pydantic `ValidationError`との衝突を避け、`RequestTimeoutError`、`InputValidationError`を使用する。

## Plugins

- pluginはcomposition rootへ明示的に渡された信頼済みPython objectだけを登録する。
- allowlist未設定時は明示的なbuilt-inだけ、空配列では何も許可せず、ID配列では列挙pluginだけを許可する。
- Manifest originの既定値は`third_party`とし、built-inは明示を必須にする。
- semantic version互換性、allowlist、plugin ID、capability競合を起動前に検証する。
- start失敗時は起動済みpluginを逆順rollbackする。
- 自動download、任意path import、hot reloadは実装しない。

Python pluginはsandboxではない。第三者pluginはCodeAtlas processと同じ権限を持つため、allowlistはcode execution security boundaryの代替にならない。

## Container and CI

- runtime containerは非root、capability drop、no-new-privilegesで動く。
- multi-stage buildでdevelopment依存をruntimeへ含めない。
- secretをbuild contextへ含めない。
- GitHub Actionsは`contents: read`だけを持ち、secret不要で動く。
- dependency cache keyは`pyproject.toml`とconstraintsに追随する。

## Deferred Controls

M2ではlocal repositoryのpath containment、SHA固定object read、basic secret/binary exclusion、bounded Git/ripgrep processを実装した。詳細は[REPOSITORY_SECURITY.md](REPOSITORY_SECURITY.md)を参照する。

M3ではsafe textだけをgeneration-scoped SQLite rowへ保存し、unsafe contentはreason codeだけを保持する。staging validation、partial unique active index、atomic publication、opaque lease、stale recovery、rollbackを実装した。DB safe errorにはSQL、絶対path、source本文を含めない。SQLite file自体は安全なsource本文を含むため、OS permission、volume access、backup、retentionをsecret相当の運用境界として扱う。

## M4 Agent Boundary

- Toolはcatalogに登録された21個のread-only Toolだけで、shell、write、network、repository mutationはない。
- 引数はPydantic strict model（`extra=forbid`、frozen）で検証し、Tool Executorがtool名allowlist、repository scope、timeout、payload上限を強制する。
- repository contentはuntrusted envelope（明示delimiter）内でのみReasonerへ渡し、evidence内の命令をsystem instructionとして扱わない。ReasonerはToolを実行できない。
- citationはTool evidenceのrepository/SHA/path/lineから直接生成し、modelが返したcitation IDは実在evidenceと照合する。invented citationは破棄する。
- model provider error、invalid JSON、timeoutはextractive fallbackへ変換し、prompt、source、tokenをlogや回答へ出さない。
- Ollama base URLはuserinfo、query、fragmentを拒否し、HTTP proxy環境変数を無視する。

## M5 Interaction Boundary

- Discord adapterはself/other-bot、対象thread、reply policyを検証し、既処理messageとduplicate interactionを無視する。
- idempotencyはSQLiteのatomic claim、会話排他はlease付きconversation lockで制御する。crashしたin-progress claimはlease expiry後に再claimできる。
- 同時thread実行のbusy応答、handler失敗時の応答は安全な文言だけで、例外内容、SQL、path、tokenを含まない。
- 会話履歴は上限件数と保持期間を持ち、`conversations purge`で削除できる。attachment本文は取得・保存しない。
- Discord HTTP errorはdiscord.pyのrate limitに従い、safe message以外を送らない。

## Deferred Controls

SSRF（HTTP Tool）、GitHub webhook署名、GitHub App権限、transactional outbox、Qdrant projection整合性、Thrift cross-language linkはM6で該当機能と同時に実装・testする。mockだけを置いて実装済みとは扱わない。
