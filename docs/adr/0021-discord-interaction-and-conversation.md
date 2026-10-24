# ADR-0021: M5 Discord Adapterと会話状態の永続化

- Status: Accepted
- Date: 2026-10-25

## Context

M5はDiscord Forumの新規投稿とスレッド内追加質問へ回答する。Discord eventはat-least-onceで重複し、同一threadで同時に複数messageが到着しうる。またBot再起動後も会話履歴と冪等性を維持する必要がある。Discord固有の型をapplication/domainへ漏らしてはならない。

## Decision

1. Discord Adapterはdiscord.pyの`Client` subclassとして`on_thread_create`と`on_message`を処理し、外部入力を`CanonicalInteraction`へ変換する。applicationは`InteractionApplicationService.handle()`だけを呼び、`CanonicalResponse`を受け取る。Discordの型は`src/codeatlas/adapters/discord/`に閉じ込める。
2. 新規Forum投稿は`on_thread_create`で初回質問として自動回答する。通常messageは設定`REPLY_POLICY`（既定`mention_or_reply`）に従う。Bot自身の投稿と、設定により他Botの投稿は無視する。
3. 冪等性はDiscord message IDから作るidempotency keyでSQLiteの`interaction_records`にatomic claimする。completed/processing/failedの重複は再処理せず、prior responseを再利用する。in-progress claimがlease切れの場合はcrash recoveryとして再claimする。
4. 会話排他はSQLiteの`conversation_locks`で行う。同じthreadの同時実行はbounded waitし、timeout時は内部情報を含まないbusy応答を返す。lease expiryで異常終了したlockを回収する。
5. 会話履歴は`conversation_messages`へ最大保持件数を指定して保存し、Reasonerへは直近N件だけ渡す。既定retentionは30日で、管理者CLIからpurgeできる。secret、attachment本文、Discord tokenは保存しない。
6. 長文応答はcode fenceを壊さないchunk分割で送信し、最大chunk数を超える場合は明示的にtruncateする。Discordのrate limitはdiscord.pyに委ね、HTTP errorはsafe message以外を表示しない。
7. Discord AdapterはPluginとして`interaction:discord` capabilityを宣言し、`DISCORD__ENABLED=false`ではcredentialなしで起動できる。起動失敗はfail fastとし、既存plugin lifecycleのrollbackに従う。

## Consequences

- Bot再起動・event重複・同時実行・process crashに対して安全に収束する。
- SQLiteが会話状態のsource of truthとなり、M6のWeb APIやCLIも同じ`InteractionApplicationService`を再利用できる。
- Discordのmessage content取得にはMessage Content Intentが必要になる可能性がある。Forum threadの初回投稿はthread starter messageから取得するため、deployment手順に必要なIntentと権限を明記する。
