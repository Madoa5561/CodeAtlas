# 不足仕様と決定待ち事項

未決事項は実装を止めず、安全側の初期値で進める。外部契約や運用費へ影響する項目はマイルストーン開始前に決定する。

| ID | 不足・曖昧さ | 暫定値 | 決定期限 |
| --- | --- | --- | --- |
| OQ-01 | GitHub 認証方式と Enterprise 対応 | GitHub App、github.com のみ | Repository Adapter 前 |
| OQ-02 | private repository の権限 | installation 単位の最小 read 権限 | 同上 |
| OQ-03 | repository 上限、サイズ、保持期間 | 設定可能な quota、無制限禁止 | Indexer 前 |
| OQ-04 | 対象 branch/commit の既定 | default branch の webhook SHA | 同上 |
| OQ-05 | Qdrant 障害時の挙動 | vector なしの degraded mode | Retrieval 前 |
| OQ-06 | embedding provider/model と再索引条件 | LLM と別設定、version 保存 | 同上 |
| OQ-07 | BM25 実装/永続化方式 | SQLite FTS5 を候補 | ADR 作成時 |
| OQ-08 | Thrift grammar の採用元とライセンス | 調査後 adapter 化 | Parser 前 |
| OQ-09 | 会話保持期間と削除要求 | 30日、管理者削除 | Discord 前 |
| OQ-10 | Discord の権限、locale、応答 SLA | 最小権限、日本語/英語 | 同上 |
| OQ-11 | HTTP allowlist の管理主体 | 管理者の明示 allowlist のみ | HTTP Tool 前 |
| OQ-12 | robots.txt/利用規約違反時の挙動 | 取得拒否 | 同上 |
| OQ-13 | 確信度の算出 | heuristic と明示 | Agent 前 |
| OQ-14 | Tool のページング、最大結果、キャンセル | cursor、上限、deadline | Tool 実装前 |
| OQ-15 | OSS の正式名称、組織、連絡先 | 仮称を維持 | 公開前 |
| OQ-16 | telemetry の送信 | 既定で外部送信なし | 運用設計時 |

## 矛盾・注意点

- 要求はフェーズ1から3までを求める一方、今回の依頼は実装開始を禁止している。今回は設計と初期骨格のみとする。
- OSS 必須ファイルの所有者や連絡先が未定であり、誤情報を避けるため公開前マイルストーンで確定する。
- SQLite と Qdrant に単一 transaction は使えないため、索引世代の切替方式が必要である。
