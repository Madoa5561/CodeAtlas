# Discord Forum の動作

## 対象チャンネル

`CODEATLAS_DISCORD__FORUM_CHANNEL_ID` で指定した Forum channel だけを対象にする。Bot は次を処理する。

- 新規 Forum 投稿（`on_thread_create`）: 投稿タイトルと本文を初回質問として自動回答する。
- Forum thread 内の追加 message: `CODEATLAS_DISCORD__REPLY_POLICY` に従って回答する。
- 通常 message への反応は設定可能で、既定は「mention または Bot への reply」。

## 応答ポリシー

| `REPLY_POLICY` | 動作 |
| --- | --- |
| `all` | 対象 thread のすべての人間の message |
| `mention` | Bot への mention を含む message |
| `reply` | Bot の投稿への reply |
| `mention_or_reply` | mention または reply（既定） |

新規 Forum 投稿は reply policy に関係なく自動回答する。`ANSWER_NEW_FORUM_POSTS=false` で無効化できる。

## 無視条件

- Bot 自身の投稿
- `IGNORE_BOTS=true`（既定）のとき、他 Bot の投稿
- 対象 Forum thread 外の message
- 既に処理済みの idempotency key（Discord message ID）
- 処理中の同一 conversation に対する重複 event

## 会話と排他

- conversation は Discord thread 単位。thread ごとに履歴を SQLite に保存する。
- 同一 thread の同時実行は conversation lock で直列化し、待機時間を超えた場合は内部情報を含まない busy 応答を返す。
- 履歴は直近 `CODEATLAS_CONVERSATION__HISTORY_LIMIT` 件を Reasoner に渡す。保持期間は既定30日で、管理者が purge できる。
- Bot 再起動後も idempotency と履歴を維持する。

## 表示

- 処理中は typing indicator を表示する。
- 回答は結論、根拠、推測、不明点、citation（repository@SHA path:line）、検索方法、確信度を含む。
- Discord の message 上限に合わせて分割送信する。code fence を途中で壊さず、必要なら fence を閉じて次の chunk で再開する。
- 分割数が上限を超える場合は明示的に truncation を表示する。
- Discord API の rate limit は discord.py に委ね、HTTP error 時は安全なエラーメッセージだけを送る。

## 権限と Intent

- 必要な Intent: `guilds`、`guild_messages`（thread/message 取得）。初回投稿本文は thread starter message から取得する。
- Bot には対象 Forum channel の view/send message、thread 作成・管理、message history の権限を最小限付与する。
- Message Content Intent が必要になる構成では、Discord Developer Portal で明示的に有効化する。0.1.0 の既定構成は thread starter message と mention 情報だけで動く。
- Bot token は環境変数または secret manager から注入し、log・回答・image へ出さない。
