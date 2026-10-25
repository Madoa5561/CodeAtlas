# ADR-0031: 複数Model Providerの選択とLifecycle

- Status: Accepted
- Date: 2026-10-25

## Context

M7は複数Model Providerを要求する。既存構成はOllamaとDeepSeekだけで、runtimeがprovider型を直接分岐していた。Reasonerはtool-callingではなくtext/JSON completionのみを使うため、providerごとのtool schema変換は不要である。一方、API shape、認証、usage、availability probeはproviderごとに異なる。

## Decision

1. 既存の`ModelProviderPort`を維持し、provider固有HTTPはadapterへ閉じ込める。OpenAIは既存`OpenAICompatibleModelProvider`を再利用し、Anthropic Messages APIとGemini generateContentは専用adapterを実装する。
2. 設定は`ollama`/`deepseek`/`openai`/`anthropic`/`gemini`を独立モデルとして持ち、有効化は最大1つに制限する。keyはprovider有効時のみ必須とする。
3. 外部providerは共通の`ExternalModelPlugin`でlifecycleを管理する。start時に課金を伴わないmetadata probeを行い、失敗してもアプリ起動は継続してdegraded状態を記録する。stopはacloseを冪等に呼ぶ。
4. Anthropicは`/v1/messages`、`x-api-key`、`anthropic-version`を使う。Geminiは`/v1beta/models/{model}:generateContent`と`key` queryを使う。どちらもresponse size、timeout、retry、safe errorをboundedにする。
5. JSON response formatは、Anthropicではsystem instructionで単一JSONを要求し、Geminiでは`responseMimeType=application/json`を指定する。ModelResponseの正規化はprovider非依存を維持する。
6. モデル選択はruntime composition rootだけで行い、application/domainはprovider名を知らない。

## Consequences

- 既存Agent pipelineを変更せずにOpenAI/Anthropic/Geminiを切り替えられる。
- 外部providerはtool-callingを使わないため、複雑なinvestigationは既存heuristc plannerとtool executorが担う。
- APIキーなしではlive検証できない。契約テストはMockTransportでURL・headers・body・usage・errorを検証する。
- Provider固有のJSON強制やusage差異はadapterで吸収するが、完全なresponse schema保証はproviderに依存する。
