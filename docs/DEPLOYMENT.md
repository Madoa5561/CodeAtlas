# Deployment

## M1 Scope

M1 containerは設定をloadし、loggingと空のPlugin Registryを初期化して停止signalを待つfoundation processである。HTTP server、Discord、Repository解析、DB/Qdrant接続は提供しない。

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

## Development Difference

開発ではDockerへsourceをmountせず、hostの`venv`とeditable installを使用する。production-style imageはwheelをbuilder stageで生成し、test/dev依存をruntime imageへ含めない。

## Secrets and Data

- secretはimage、Dockerfile、Composeへ書かない。
- 必要になった時点でenvironmentまたはsecret managerから注入する。
- `.env`はGit管理対象外であり、Composeの自動読込にsecretを置く場合も配布しない。
- backup/migrationはDB実装前のため未提供である。

## Shutdown

SIGTERM/SIGINTでPlugin lifecycleを逆順停止する。shutdown timeoutを超えた場合は停止処理をcancelしてprocessを非zero終了させ、container orchestratorへ失敗を通知する。
