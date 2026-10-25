# ADR-0022: 0.1.0の配布・デプロイ方式

- Status: Accepted
- Date: 2026-10-25

## Context

ロードマップのリリースゲート`0.1.0`はM5とsecurity testを満たすことを要求する。実運用ではDiscord Bot、SQLite、local repository、Ollamaが必要で、Qdrant、GitHub App、Web APIはM6である。配布物は再現可能で、secretをimageへ含めず、tagから同じ成果物を再生成できる必要がある。

## Decision

1. `0.1.0`はM4（Agent + Ollama）とM5（Discord Forum）までの完成を対象とし、M6（GitHub/Webhook/Qdrant/Hybrid Retrieval）とM7を明示的に除外する。README/CHANGELOGに未実装を明記する。
2. 配布はPyPI公開ではなく、GitHub Releaseのsdist/wheelとGHCR container image（`ghcr.io/<owner>/codeatlas:vX.Y.Z`、`X.Y.Z`、`latest`）とする。tag `vX.Y.Z`をpushすると`.github/workflows/release.yml`がquality gate、build、image publish、release asset添付を行う。本番deployは`vX.Y.Z`へ固定する。
3. 実行構成は単一`codeatlas` container + host上のOllamaとする。Composeでは`host.docker.internal`（Linuxは`host-gateway`）でOllamaへ接続し、Discord token等はenvironment/secretから注入する。`qdrant` profileはM6準備として残すが0.1.0では接続しない。
4. repositoryは`CODEATLAS_REPOSITORY__REGISTERED_PATHS`で明示承認されたlocal directoryのみを登録し、containerへはread-only mountする。startup時にschemaを検証し、migrationは`codeatlas-admin db upgrade`で明示実行する。
5. secretは`.env`を配布せず、deployment環境のsecret機能から渡す。Discord token、Ollama URL、repository pathをlogやrelease noteへ含めない。
6. `0.1.0`の受け入れは、clean Linux containerでのpytest/Ruff/mypy/coverage/build、Discordを使わないoffline E2E（index→agent→citation）、Ollama接続時のlive E2E、Compose起動、security test（prompt injection、重複event、thread同時実行、tool authorization、secret非出力）の成功とする。

## Consequences

- 0.1.0は「Discord Forumで根拠付き回答ができるMVP」として一貫したscopeになる。
- GHCR imageとcomposeにより、利用者はsource buildなしで起動できる。
- M6のWebhook/Web API/Qdrantは別release（public beta）まで提供されない。
