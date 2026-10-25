# ADR-0028: GitHub Push WebhookによるIncremental Index更新

- Status: Accepted
- Date: 2026-10-25

## Context

ADR-0026ではWebhookの署名検証と冪等記録までを実装し、push eventからの自動index更新を後続とした。M6はmanual syncとWebhookによるindex更新を要求する。Webhookはat-least-onceであり、連続pushや同時配送があり得るため、index処理を直列化・coalesceする必要がある。

## Decision

1. Webhookで検証・記録済みのfreshな`push`だけを、設定された`github.webhook_repository`（owner/name）とpayloadの`repository.full_name`が一致する場合にindex対象とする。
2. `github.webhook_repository_id`で登録済みlocal repositoryを指定し、`WebhookSyncService`が`TransactionalIndexingService.create_index(IndexingRequest(..., revision="HEAD", incremental=True))`をbackground taskで実行する。
3. index処理はasyncio.Lockで直列化し、実行中に追加pushが来た場合は1回のfollow-upへcoalesceする。配送ごとに無制限の並列indexは作らない。
4. Webhook HTTP responseは202を返し、`indexing`に`scheduled`/`ignored`/`duplicate`/`disabled`を示す。index完了は待たない。
5. 失敗は`webhook_index_failed`として安全にログし、Webhook自体は成功扱いにする。冪等ledgerは配送ID単位で保持する。
6. local cloneの`git fetch`は行わない。host側のclone更新が前提であり、同期されていない場合はindexが安全に失敗する。manual syncは既存の`codeatlas-admin index incremental`を使う。
7. shutdown時は実行中taskをcancelし、API server停止後にindex serviceを解放する。

## Consequences

- GitHub pushからindex更新までが自動化され、次の質問から新しいcommitを参照できる（local clone更新済みの場合）。
- Webhook応答はindex完了を保証しない。完了確認はlog/generationを参照する。
- GitHub Appのinstallation token更新、git fetch、queueの永続化は未実装であり、将来のworker分離で強化する。
