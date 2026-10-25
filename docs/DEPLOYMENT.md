# Deployment

## 0.1.0 Scope

0.1.0 は M4（Agent + Ollama）と M5（Discord Forum）までのMVPである。次は含まない。

- GitHub App / Webhook / PR / Issue Tool
- Qdrant / Vector Search / Hybrid Retrieval
- Web API / 管理画面 / MCP / VS Code 拡張
- Thrift parser / 確定 Call Graph

単一の `codeatlas` container と host 上の Ollama で構成する。SQLite が source of truth である。

## Prerequisites

- Docker Engine と Docker Compose plugin
- Ollama（省略可。無効時は deterministic degraded mode）
- Discord Bot token と Forum channel ID（Discord を使う場合）
- 解析対象 repository の local clone（read-only mount 用）

## Configuration

```powershell
Copy-Item .env.example .env
```

`.env` は Git 管理外である。最低限、次を設定する。

```text
CODEATLAS_REPOSITORY__REGISTERED_PATHS=["/repositories/linejs"]
CODEATLAS_OLLAMA__ENABLED=true
CODEATLAS_OLLAMA__MODEL=ornith-1.5-9b:q4_k_m
CODEATLAS_OLLAMA__BASE_URL=http://host.docker.internal:11434
CODEATLAS_DISCORD__ENABLED=true
CODEATLAS_DISCORD__TOKEN=...
CODEATLAS_DISCORD__FORUM_CHANNEL_ID=123456789012345678
```

secret は `.env` を配布せず、本番では orchestrator の secret 機構から環境変数として注入する。

## Build and Start

```powershell
docker compose build
docker compose up -d codeatlas
docker compose ps
docker compose logs -f codeatlas
```

release image を使う場合は build を省略できる。versionを固定するため`CODEATLAS_IMAGE_TAG`へ明示tagを設定する。

```powershell
$env:CODEATLAS_IMAGE_TAG="v0.1.2"
docker compose pull
docker compose up -d codeatlas
```

GHCR tagは`vX.Y.Z`、`X.Y.Z`、`latest`を公開する。本番では`latest`ではなく`vX.Y.Z`へ固定する。

container は UID/GID 10001 の非rootで、`read_only`、`no-new-privileges`、`cap_drop: ALL`、`/tmp` tmpfs で動く。書き込み可能なのは `codeatlas-data` volume（`/app/data`）だけである。

## Repository Mount and Index

対象 repository は明示承認した directory だけを read-only mount する。

```yaml
services:
  codeatlas:
    volumes:
      - codeatlas-data:/app/data
      - /approved/repositories/linejs:/repositories/linejs:ro
```

`CODEATLAS_REPOSITORY__REGISTERED_PATHS` には container 内 path（`/repositories/linejs`）を書く。

初回 migration と index 作成は明示操作である。service 起動時に自動 migration は行わない。

```powershell
docker compose run --rm codeatlas codeatlas-admin --json db upgrade
docker compose run --rm -v C:\work\linejs:/repositories/linejs:ro codeatlas `
  codeatlas-admin --json index full /repositories/linejs HEAD
docker compose run --rm codeatlas codeatlas-admin --json index incremental /repositories/linejs HEAD
```

index 作成には `git` が必要で、image に含まれる。working tree は読まず、解決した commit SHA の tree/blob だけを解析する。index 後に `docker compose up -d` で service を起動する。

## Health and Shutdown

- service は起動完了後に `/tmp/codeatlas-ready` へ PID を公開し、`codeatlas-healthcheck` がその生存を検証する。
- `SIGTERM`/`SIGINT` で plugin を逆順停止する。shutdown timeout を超えた場合は非zero終了する。
- schema が head でない、または設定が不正な場合は起動に失敗する。`docker compose logs` に安全なエラーコードだけが出る。

## Ollama

host の Ollama を container から `host.docker.internal` で利用する（Linux は `extra_hosts: host-gateway`、Compose に設定済み）。

```bash
ollama pull ornith-1.5-9b:q4_k_m
ollama serve
```

- Ollama が到達不能でも CodeAtlas は起動し、`degraded` mode で evidence 列挙型の回答を返す。
- thinking model では `CODEATLAS_OLLAMA__THINK=true` を検討する。reasoning text は回答本文として扱わない。
- 回答生成は `CODEATLAS_AGENT__DEADLINE_SECONDS` と `CODEATLAS_OLLAMA__TIMEOUT_SECONDS` で制限される。

## Discord

Discord Developer Portal で Bot を作成し、次を設定する。

- Privileged Intent: Message Content Intent を有効化する（Forum 投稿/スレッド本文の取得に必要）
- 対象 Forum channel の権限: View Channel、Send Messages、Send Messages in Threads、Read Message History
- `CODEATLAS_DISCORD__FORUM_CHANNEL_ID` に対象 Forum channel ID を設定
- `CODEATLAS_DISCORD__REPLY_POLICY` は既定 `mention_or_reply`

動作仕様は [DISCORD_BEHAVIOR.md](DISCORD_BEHAVIOR.md) を参照する。Bot の重複 event は SQLite の idempotency ledger で排除し、同一 thread は conversation lock で直列化する。

## Retention and Backup

```powershell
docker compose run --rm codeatlas codeatlas-admin --json conversations purge --days 30
```

- `/app/data/codeatlas.db` を migration 前に backup する。
- downgrade は conversation schema を削除する破壊的操作である。`0001_m3_transactional_index` への downgrade 前に停止と backup を行う。
- SQLite は安全な source 本文と会話履歴を含むため、volume access と backup を secret 相当の運用境界として扱う。

## Release Artifacts

tag `vX.Y.Z` の push で `.github/workflows/release.yml` が次を実行する。

1. Ruff、mypy、pytest、coverage、package build
2. GHCR への container image push（`ghcr.io/<owner>/codeatlas:X.Y.Z` と `latest`）
3. GitHub Release へ sdist/wheel を添付し、release notes を生成

手動で再実行する場合は `workflow_dispatch` に既存 tag を渡す。

## Secrets

- secret を image、Dockerfile、Compose file、GitHub Actions に直書きしない。
- log には token、password、secret、authorization、api_key と credential 形式の文字列が redaction されるが、そもそも message へ連結しない。
- Discord token を rotate した場合は container を再起動する。
