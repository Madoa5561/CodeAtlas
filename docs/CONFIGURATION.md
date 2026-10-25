# Configuration

## Loading

CodeAtlasはPydantic Settingsで起動時に設定を検証する。環境変数prefixは`CODEATLAS_`、section区切りは`__`である。

```text
CODEATLAS_LOGGING__FORMAT=json
CODEATLAS_INDEXING__MAX_CONCURRENCY=4
```

process environmentが`.env`より優先される。不正な型、範囲外の値、有効化されたintegrationの必須secret不足は起動時に失敗する。

## Sections

| Section | 目的 | 状態 |
| --- | --- | --- |
| `APPLICATION` | environment、service name、shutdown timeout | 利用 |
| `LOGGING` | level、JSON/console | 利用 |
| `DATABASE` | SQLite URL、busy/lease/lock timeout | 利用 |
| `QDRANT` | URL、API key、timeout | M6まで未接続 |
| `OLLAMA` | base URL、model、retry、timeout、thinking | M4で利用 |
| `DEEPSEEK` | API key、base URL、model、retry、thinking | 利用（Ollamaと排他） |
| `DISCORD` | enabled、token、forum ID、reply policy、表示 | M5で利用 |
| `GITHUB` | enabled、token、webhook secret、API URL | M6まで未接続 |
| `SECURITY` | plugin allowlist、read-only、HTTP上限 | 利用 |
| `INDEXING` | file/repository/concurrency/timeout/parser/failure上限 | M3で利用 |
| `REPOSITORY` | Git/ripgrep executable、limits、起動時登録path | M2/M4で利用 |
| `AGENT` | 調査budget、model timeout、planner方針 | M4で利用 |
| `CONVERSATION` | 履歴件数、保持期間、thread lock | M5で利用 |

全項目と既定値は[`.env.example`](../.env.example)を参照する。

## Secrets

token、API key、webhook secretは`SecretStr`として保持され、reprではmaskされる。`.env`をcommitせず、本番ではorchestratorのsecret機能等から環境変数を注入する。Docker imageやCompose fileにsecret値を埋め込まない。

Discord、GitHub、Ollama、Qdrantは`ENABLED=false`ならcredentialなしで起動できる。

## Repository Registration

`CODEATLAS_REPOSITORY__REGISTERED_PATHS`はservice起動時に登録するlocal repository pathの明示listである。未設定・空listではrepositoryを登録せず、Toolは`invalid_input`を返す。pathは管理者が承認したread-only mountだけを指定する。Repository IDはGit directory identityから決まり、同じrepositoryを別の綴りで登録しても重複しない。

登録だけではindexは作られない。初回は`codeatlas-admin index full <path> <revision>`、差分は`codeatlas-admin index incremental`を実行する。

## Ollama

```text
CODEATLAS_OLLAMA__ENABLED=true
CODEATLAS_OLLAMA__BASE_URL=http://localhost:11434
CODEATLAS_OLLAMA__MODEL=ornith-1.5-9b:q4_k_m
CODEATLAS_OLLAMA__THINK=false
CODEATLAS_OLLAMA__TIMEOUT_SECONDS=60
CODEATLAS_OLLAMA__MAX_RETRIES=2
CODEATLAS_OLLAMA__RETRY_BACKOFF_SECONDS=0.5
CODEATLAS_OLLAMA__NUM_CTX=8192
```

- `ENABLED=true`では`MODEL`が必須。起動時に`/api/tags`へbounded probeを行い、到達不能でも起動は継続してdegraded modeになる。
- `THINK=false`が既定。thinking modelでは`true`にできるが、reasoning textは回答本文として扱わない。
- base URLにuserinfo、query、fragmentは指定できない。
- Ollamaが無効・停止・timeoutのとき、Agentはdeterministic extractive reasonerで回答し、statusを`partial`にする。

## DeepSeek

DeepSeek APIはOpenAI互換であり、`DeepSeekModelProvider`が同じcanonical contractへ正規化する。**OllamaとDeepSeekは同時に有効化できない**（起動時validation error）。

```text
CODEATLAS_OLLAMA__ENABLED=false
CODEATLAS_DEEPSEEK__ENABLED=true
CODEATLAS_DEEPSEEK__API_KEY=sk-...
CODEATLAS_DEEPSEEK__BASE_URL=https://api.deepseek.com
CODEATLAS_DEEPSEEK__MODEL=deepseek-flash
CODEATLAS_DEEPSEEK__TIMEOUT_SECONDS=60
CODEATLAS_DEEPSEEK__MAX_RETRIES=2
CODEATLAS_DEEPSEEK__RETRY_BACKOFF_SECONDS=0.5
CODEATLAS_DEEPSEEK__THINKING=false
```

- `MODEL`は`deepseek-flash`または`deepseek-v4-pro`。既定は`deepseek-flash`。
- `THINKING=false`が既定で、DeepSeekのthinking modeを無効化する。有効化しても`reasoning_content`は回答本文として扱わない。
- API keyは`SecretStr`で保持し、log・回答・imageへ出さない。401/403は安全なprovider errorへ変換する。
- 到達不能・timeout・rate limit時はbounded retry後、あるいはextractive fallbackで`partial`回答になる。
- `/models`へのbounded probeで起動時availabilityを記録する（到達不能でも起動は継続）。

## Discord

```text
CODEATLAS_DISCORD__ENABLED=true
CODEATLAS_DISCORD__TOKEN=...
CODEATLAS_DISCORD__FORUM_CHANNEL_ID=123456789012345678
CODEATLAS_DISCORD__REPLY_POLICY=mention_or_reply
CODEATLAS_DISCORD__HISTORY_LIMIT=12
CODEATLAS_DISCORD__TYPING_INTERVAL_SECONDS=8
CODEATLAS_DISCORD__MAX_RESPONSE_CHARS=1900
CODEATLAS_DISCORD__LOCALE=ja
CODEATLAS_DISCORD__IGNORE_BOTS=true
CODEATLAS_DISCORD__ANSWER_NEW_FORUM_POSTS=true
```

`REPLY_POLICY`は`all`、`mention`、`reply`、`mention_or_reply`（既定）。新規Forum投稿は`ANSWER_NEW_FORUM_POSTS`に従って自動回答する。詳細は[DISCORD_BEHAVIOR.md](DISCORD_BEHAVIOR.md)を参照する。

## Agent Budgets

`CODEATLAS_AGENT__*`で1質問あたりのiteration、tool call、deadline、evidence、plan step、回答文字数、temperature、output token、model timeoutを制限する。budget超過は`agent_budget_exceeded`として安全な回答へ変換される。`USE_MODEL_PLANNER`は既定`false`でdeterministic plannerを使う。

## Conversation

`CODEATLAS_CONVERSATION__HISTORY_LIMIT`はReasonerへ渡す直近履歴数、`RETENTION_DAYS`は保持期間（既定30日）、`THREAD_LOCK_WAIT_SECONDS`と`THREAD_LOCK_LEASE_SECONDS`は同一threadの排他制御に使う。保持期間を超えた履歴は`codeatlas-admin conversations purge`で削除できる。

## Plugin Allowlist

`CODEATLAS_SECURITY__PLUGIN_ALLOWLIST`は次の三状態を持つ。

- 未設定: Manifestで`built_in`と明示されたpluginだけを許可する。
- 空配列`[]`: built-inを含むすべてのpluginを拒否する。
- plugin ID配列: 列挙されたIDだけを許可する。

Manifestのorigin既定値は`third_party`であり、第三者pluginが暗黙にbuilt-inになることはない。repositoryや任意pathからpluginをloadする機能はない。

## Repository Limits

`CODEATLAS_REPOSITORY__*`でGit/ripgrep executable、2 GiB repository、1 MiB text/search file、1,000 directory entries、100 search hits、2 MiB diff、4 MiB command output、30秒timeoutの既定値を変更できる。Pydanticが安全な上限・下限を起動時に検証する。executableは`git(.exe)`と`rg(.exe)`だけを許可する。

## Database and Migration

`CODEATLAS_DATABASE__LEASE_TIMEOUT_SECONDS`はindexingのprocess間lease、`LOCK_WAIT_TIMEOUT_SECONDS`はprocess内repository lock待機を制限する。Database URLはSQLiteだけを許可する。

migrationは起動時に自動適用しない。`codeatlas-admin db upgrade`を明示実行し、serviceはschemaがheadでない場合に安全に停止する。0.1.0のheadは`0002_m4_conversation`で、M3のindex schemaとM5のconversation schemaを含む。
