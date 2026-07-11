# リスクと Threat Model

## リスク登録簿

| ID | リスク | 影響 | 可能性 | 対応 |
| --- | --- | --- | --- | --- |
| R-01 | repository content の Prompt Injection | 高 | 高 | data/instruction 分離、Tool allowlist、出力検査 |
| R-02 | HTTP Tool の SSRF/DNS rebinding | 高 | 中 | scheme/host/IP を接続時と redirect 毎に検証 |
| R-03 | 秘密情報の索引化・回答・ログ出力 | 高 | 中 | path/content scanner、deny pattern、redaction |
| R-04 | stale index による誤った行番号 | 高 | 中 | evidence に SHA 必須、世代を atomic publish |
| R-05 | Discord/GitHub/LLM rate limit | 中 | 高 | queue、bounded retry、jitter、backpressure |
| R-06 | 大規模 monorepo の資源枯渇 | 高 | 高 | quota、deadline、サイズ制限、差分索引 |
| R-07 | Parser の誤解析 | 中 | 高 | confidence/provenance、golden test |
| R-08 | Provider の Tool Calling 差異 | 中 | 高 | canonical schema、contract test |
| R-09 | SQLite/Qdrant の部分失敗 | 高 | 中 | generation staging、idempotency、reconciliation |
| R-10 | OSS supply-chain compromise | 高 | 中 | pin、lock、Dependabot、SBOM を計画 |
| R-11 | ライセンス/利用規約違反 | 高 | 低-中 | allowlist、robots/ToS、依存監査 |
| R-12 | Bot の重複/自己反応ループ | 中 | 中 | event ledger、author filter、thread lock |

## 保護対象と Trust Boundary

- GitHub/Discord/LLM の認証情報と private repository のデータ
- 会話履歴、索引の SHA 整合性、回答根拠
- Discord/GitHub webhook、repository content、Model Provider、外部 HTTP、DB の各境界

## 主要脅威と統制

| 脅威 | 予防 | 検知/復旧 |
| --- | --- | --- |
| spoofed webhook | 署名、timestamp、body size 検証 | event ID 監査 |
| Prompt Injection | untrusted envelope、read-only tool | Tool trace、攻撃テスト |
| path traversal/symlink escape | canonical root containment | 拒否ログ、security test |
| SSRF | allowlist、IP deny、redirect 再検証 | destination audit、timeout |
| data exfiltration | secret denylist、egress 制限 | redaction、credential rotation |
| DoS | quota、bounded concurrency、result limit | queue depth、circuit breaker |
| stale/poisoned index | SHA verification、staging | prior generation へ rollback |

## セキュリティ検証条件

- localhost、private、link-local、metadata、IPv6、redirect、DNS rebinding 相当を拒否する。
- `.env`、鍵、token 候補、巨大/バイナリファイルを索引対象外にする。
- `../`、絶対パス、symlink escape で root 外を読めない。
- repository 内の命令を実行せず、shell/write Tool が存在しない。
- webhook replay、duplicate event、同一 thread concurrency を安全に処理する。
