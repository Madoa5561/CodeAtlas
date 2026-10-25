# ADR-0026: ローカルWeb APIとGitHub Webhook受信

- Status: Accepted
- Date: 2026-10-25

## Context

M6はWeb APIとGitHub Webhookを要求する。Discord以外のUIからも同じInteractionApplicationServiceを利用できる必要がある。GitHub Webhookは署名検証とat-least-once配送への冪等性が必要で、payloadを信頼してはならない。0.2.1ではWebhook受信と冪等記録までを実装し、push eventからの自動index更新は後続段階で接続する。

## Decision

1. FastAPIアプリを`adapters/http/api.py`に置き、`/healthz`、`/repositories`、`/ask`、`/webhooks/github`を提供する。domain/applicationへFastAPIを漏らさない。
2. `/ask`は`CanonicalInteraction`を構築し、`InteractionApplicationService.handle(repository_scope=...)`を呼ぶ。既存のAgent/Tool/conversation lock/idempotencyをそのまま再利用し、UI固有DTOを返さない。
3. uvicornはアプリと同じevent loop上でbackground taskとして起動するplugin（`interaction:http-api`）とする。アプリのSIGINT/SIGTERM handlerを維持するためuvicornのsignal captureを無効化する。
4. WebhookはHMAC-SHA256（`X-Hub-Signature-256`）を`hmac.compare_digest`で検証し、body size上限を適用する。secret未設定時は503、署名不正は401、delivery ID欠落は400とする。
5. 検証済みdelivery IDはSQLite `webhook_deliveries`へINSERT ON CONFLICTで記録し、重複は202 `duplicate=true`として再処理しない。payload本文は保存しない。
6. APIは既定で無効・`127.0.0.1` bindとし、`/ask`の無効化設定を持つ。認証はローカル管理者境界を前提とし、公開ネットワークへ直接公開しない。
7. 自動index更新（Webhook → fetch → index）は別段階とする。現在のWebhookは受信・検証・冪等記録のみで、運用者はlocal clone更新後に`codeatlas-admin index incremental`を実行する。

## Consequences

- Discord以外から同じcoreをHTTPで利用でき、Webhookのreplay/duplicateを安全に排除できる。
- Webhookはまだindexを更新しないため、GitHub pushから回答反映までの自動化は未完である。次段階でrepository mappingとindex workerを追加する。
- uvicorn embedded起動はsignal ownershipをアプリ側に残すが、将来Web APIを独立processへ分離する場合はこの判断を見直す。
