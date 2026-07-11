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
| `DATABASE` | SQLite URL、busy timeout | 検証のみ |
| `QDRANT` | URL、API key、timeout | 検証のみ、接続なし |
| `OLLAMA` | base URL、model、timeout | 検証のみ、接続なし |
| `DISCORD` | enabled、token、forum ID | 検証のみ、adapterなし |
| `GITHUB` | enabled、token、webhook secret、API URL | 検証のみ、adapterなし |
| `SECURITY` | plugin allowlist、read-only、HTTP上限 | 一部利用 |
| `INDEXING` | file/repository/concurrency/timeout上限 | 検証のみ |

全項目と既定値は[`.env.example`](../.env.example)を参照する。

## Secrets

token、API key、webhook secretは`SecretStr`として保持され、reprではmaskされる。`.env`をcommitせず、本番ではorchestratorのsecret機能等から環境変数を注入する。Docker imageやCompose fileにsecret値を埋め込まない。

Discord、GitHub、Ollama、Qdrantは`ENABLED=false`ならcredentialなしで起動できる。M1ではいずれも外部接続しない。

## Plugin Allowlist

`CODEATLAS_SECURITY__PLUGIN_ALLOWLIST`はJSON配列で指定する。空配列は、composition rootへ明示的に渡されたpluginを許可する。非空の場合、列挙されたplugin IDだけを登録できる。repositoryや任意pathからpluginをloadする機能はない。
