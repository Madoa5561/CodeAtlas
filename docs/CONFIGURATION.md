# Configuration

## Loading

CodeAtlasはPydantic Settingsで起動時に設定を検証する。環境変数prefixは`CODEATLAS_`、section区切りは`__`である。

```text
CODEATLAS_LOGGING__FORMAT=json
CODEATLAS_INDEXING__MAX_CONCURRENCY=4
```

process environmentが`.env`より優先される。不正な型、範囲外の値、有効化されたintegrationの必須secret不足は起動時に失敗する。

## Sections

| Section | 目的 | M1の利用 |
| --- | --- | --- |
| `APPLICATION` | environment、service name、shutdown timeout | 利用 |
| `LOGGING` | level、JSON/console | 利用 |
| `DATABASE` | SQLite URL、busy/lease/lock timeout | M3で利用 |
| `QDRANT` | URL、API key、timeout | 検証のみ、接続なし |
| `OLLAMA` | base URL、model、timeout | 検証のみ、接続なし |
| `DISCORD` | enabled、token、forum ID | 検証のみ、adapterなし |
| `GITHUB` | enabled、token、webhook secret、API URL | 検証のみ、adapterなし |
| `SECURITY` | plugin allowlist、read-only、HTTP上限 | 一部利用 |
| `INDEXING` | file/repository/concurrency/timeout/parser/failure上限 | M3で利用 |
| `REPOSITORY` | Git/ripgrep executable、read/search/diff/command上限 | M2で利用 |

全項目と既定値は[`.env.example`](../.env.example)を参照する。

## Secrets

token、API key、webhook secretは`SecretStr`として保持され、reprではmaskされる。`.env`をcommitせず、本番ではorchestratorのsecret機能等から環境変数を注入する。Docker imageやCompose fileにsecret値を埋め込まない。

Discord、GitHub、Ollama、Qdrantは`ENABLED=false`ならcredentialなしで起動できる。M1ではいずれも外部接続しない。

## Plugin Allowlist

`CODEATLAS_SECURITY__PLUGIN_ALLOWLIST`は次の三状態を持つ。

- 未設定: Manifestで`built_in`と明示されたpluginだけを許可する。
- 空配列`[]`: built-inを含むすべてのpluginを拒否する。
- plugin ID配列: 列挙されたIDだけを許可する。

Manifestのorigin既定値は`third_party`であり、第三者pluginが暗黙にbuilt-inになることはない。repositoryや任意pathからpluginをloadする機能はない。

## Repository Limits

`CODEATLAS_REPOSITORY__*`でGit/ripgrep executable、2 GiB repository、1 MiB text/search file、1,000 directory entries、100 search hits、2 MiB diff、4 MiB command output、30秒timeoutの既定値を変更できる。Pydanticが安全な上限・下限を起動時に検証する。executableは`git(.exe)`と`rg(.exe)`だけを許可する。

## M3 Database and Indexing

`CODEATLAS_DATABASE__LEASE_TIMEOUT_SECONDS`はprocess間lease、`LOCK_WAIT_TIMEOUT_SECONDS`はprocess内repository lock待機を制限する。`CODEATLAS_INDEXING__MAX_PARSER_NODES`、`MAX_PARSER_DEPTH`、`MAX_FAILURE_RATE`、`EXCLUDE_GENERATED`、`EXCLUDE_MINIFIED`はactivation前のresource/failure policyへ含まれ、変更時はincremental reuseを無効化する。

Database URLはSQLiteだけを許可する。migrationは起動時に自動適用せず、`codeatlas-admin db upgrade`を明示実行する。
