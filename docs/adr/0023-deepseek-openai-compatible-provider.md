# ADR-0023: DeepSeekをOpenAI互換Providerとして追加

- Status: Accepted
- Date: 2026-10-25

## Context

M4の最初のModel ProviderはOllamaだった。運用環境によってはlocal GPU/Ollamaを置かず、DeepSeek APIのAPI keyだけで動かしたいという要求がある。DeepSeek APIはOpenAI互換の`POST /chat/completions`を提供し、`deepseek-flash`と`deepseek-v4-pro`、JSON output、thinking mode、tool callsを持つ。domainは特定providerへ依存できない。

## Decision

1. `OpenAICompatibleModelProvider`をadapterとして実装し、OpenAI形式のrequest/responseをcanonical `ModelRequest`/`ModelResponse`/`CanonicalToolCall`へ正規化する。DeepSeekはその薄い設定wrapper（`DeepSeekModelProvider`）とする。
2. DeepSeek固有の差分（base URL `https://api.deepseek.com`、model名、`thinking: {"type": ...}`、`reasoning_content`の無視）はadapter内に閉じ込め、application/domainへprovider名を漏らさない。
3. 設定は`CODEATLAS_DEEPSEEK__*`とし、`api_key`は`SecretStr`、`enabled=true`では必須。OllamaとDeepSeekの同時有効化はroot validatorで拒否する。provider選択はcomposition root（`runtime.py`）だけが行う。
4. `thinking=false`を既定とし、JSON outputが必要なReasonerでは`response_format={"type":"json_object"}`を送る。`reasoning_content`は回答本文として扱わない。
5. retryはconnect/timeout/429/5xxに限定し、401/403/400は即時に安全なprovider errorへ変換する。error/repr/logにAPI key、prompt、response body、credentialed URLを含めない。`trust_env=false`、redirect非追従。
6. 起動時は`GET /models`をbounded probeし、到達不能でも起動は継続する（degraded mode）。agentはextractive fallbackで`partial`回答を返す。

## Consequences

- OllamaなしでDeepSeek API keyだけでMVPを運用できる。
- OpenAI互換gatewayへの拡張は同じadapterで可能だが、provider名・payload差分は更にadapterへ追加する必要がある。
- 同時有効化を禁止したため、provider切替は設定変更と再起動で行う。M6以降で`MODEL__PROVIDER` selectorを導入する余地を残す。
