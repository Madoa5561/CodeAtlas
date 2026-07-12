# Deployment

## M3 Scope

Container processはfoundation lifecycleを実行する。M3のSQLite migration/indexingは`codeatlas-admin`による明示管理操作であり、service起動時に自動実行しない。HTTP server、Discord、Qdrant接続は提供しない。

## Production-style Compose

```powershell
docker compose build
docker compose up -d codeatlas
docker compose ps
docker compose logs codeatlas
docker compose down
```

imageはUID/GID 10001の非root userで動き、source codeをhost mountしない。SQLite将来利用向けに`codeatlas-data` volumeを`/app/data`へmountする。healthcheckは起動完了時に公開されたPIDの生存状態を検証する。

Qdrant定義は任意profileに隔離される。

```powershell
docker compose --profile qdrant up -d
```

M1のCodeAtlasはQdrantへ接続しない。profileの起動は将来構成のcontainer検証用途だけである。

M2でlocal repositoryをcontainerから利用する場合は、管理者が対象directoryだけを明示的にread-only mountする。Compose既定構成はhost repositoryをmountしない。

```yaml
services:
  codeatlas:
    volumes:
      - codeatlas-data:/app/data
      - /approved/repository:/repositories/project:ro
```

## Development Difference

開発ではDockerへsourceをmountせず、hostの`venv`とeditable installを使用する。production-style imageはwheelをbuilder stageで生成し、test/dev依存をruntime imageへ含めない。

## Secrets and Data

- secretはimage、Dockerfile、Composeへ書かない。
- 必要になった時点でenvironmentまたはsecret managerから注入する。
- `.env`はGit管理対象外であり、Composeの自動読込にsecretを置く場合も配布しない。
- migration前に`/app/data/codeatlas.db` volumeをbackupする。downgradeはM3 index tableを削除する破壊的な明示操作である。

## M3 Migration

```powershell
docker compose run --rm codeatlas codeatlas-admin --json db upgrade
docker compose run --rm codeatlas codeatlas-admin --json db current
```

Repositoryをcontainerからindexする場合は承認済みdirectoryだけをread-only mountする。SQLite data volumeだけが書込み可能である。

## Shutdown

SIGTERM/SIGINTでPlugin lifecycleを逆順停止する。shutdown timeoutを超えた場合は停止処理をcancelしてprocessを非zero終了させ、container orchestratorへ失敗を通知する。
