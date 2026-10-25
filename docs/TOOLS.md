# Tool Interface と実装状況

## 共通契約

すべての Tool は `src/codeatlas/application/tools/schemas.py` の Pydantic strict model で引数を検証し、`ToolDefinition` の JSON Schema を model へ公開する。実行は必ず `ToolExecutor` を通り、次を強制する。

- tool 名 allowlist と catalog 一致
- repository scope authorization（`ToolContext.allowed_repository_ids`）
- 1回の tool call の timeout
- payload の文字数上限と truncation 表示
- 分類済みの安全な error（`invalid_input`、`not_found`、`forbidden`、`timeout`、`rate_limited`、`provider_error`、`limit_exceeded`、`internal_error`）
- SHA 固定の evidence（repository、commit SHA、POSIX path、1始まり行番号、excerpt、retrieval method）

Tool は repository の working tree を読まない。revision 未指定時は active index generation の commit SHA を使い、index が無い場合だけ repository の既定 branch を解決する。

## Repository / 検索 Tool（0.1.0）

| Tool | 主な引数 | 戻り値の要点 |
| --- | --- | --- |
| `list_repositories` | なし | 登録済み repository の ID/name/default branch |
| `get_repository` | `repository` | repository メタデータ |
| `list_branches` | `repository` | branch と解決済み SHA |
| `list_tags` | `repository` | tag と解決済み SHA |
| `list_directory` | `repository`, `path`, `revision` | tree entry と evidence |
| `read_file` | `repository`, `path`, `revision` | 安全な text と行範囲 |
| `search_text` | `repository`, `query`, `regex`, `path_prefix` | ripgrep/Python fallback の hit と evidence |
| `search_code` | `repository`, `query` | symbol 検索と text 検索の統合 |
| `search_lexical` | `repository`, `query`, `path_prefix` | FTS5/BM25 の行単位lexical検索 |
| `search_vector` | `repository`, `query`, `limit` | Qdrant cosine vector検索（optional） |
| `fetch_http` | `repository_id`, `url`, `method` | 許可ドメインのGET/HEAD、SSRF対策・本文抽出 |
| `traverse_graph` | `repository`, `start`, `edge_kinds`, `direction`, `max_depth` | call/dependency/reference のbounded BFS |
| `get_pull_request` | `repository_id`, `repository`(owner/name), `number` | GitHub PR本文・state・head/base SHA |
| `get_pull_request_diff` | `repository_id`, `repository`, `number` | GitHub PRのbounded unified diff |
| `list_issues` | `repository_id`, `repository`, `state`, `limit` | GitHub issue一覧（PR除外） |
| `find_symbol` | `repository`, `name`, `kind` | 宣言と source span |
| `find_definition` | `repository`, `name` | 定義候補（宣言優先） |
| `find_exports` | `repository`, `name` or `path` | export宣言、export relation、package/deno manifests経由の公開entrypoint再export |
| `find_references` | `repository`, `name` | reference/call/extends/implements 候補 |
| `find_callers` | `repository`, `name` | call 候補の source 側 |
| `find_callees` | `repository`, `name` | call 候補の target 側 |
| `get_dependencies` | `repository`, `path` | import 候補 |
| `get_dependents` | `repository`, `path` | import 元候補 |
| `get_commit` | `repository`, `revision` | commit metadata |
| `search_commits` | `repository`, `query`, filters | commit 一覧 |
| `get_file_history` | `repository`, `path` | rename 追跡を含む履歴 |
| `get_blame` | `repository`, `path`, `revision` | commit 固定の blame |
| `compare_refs` | `repository`, `base`, `head` | 安全な diff |
| `get_diff` | `repository`, `revision` | 安全な diff |

`get_pull_request`、`get_pull_request_diff`、`list_issues` は GitHub App/API が必要なため M6 で実装する。0.1.0 の tool catalog には表示しない。`find_callers` などの call 関係は parser の call candidate であり、確定 Call Graph ではない（confidence と provenance を付与する）。

`find_exports` は、宣言の `exported` 属性と export relation に加え、`package.json` / `deno.json` の `exports` マップと entrypoint の再export構文（`export { Client } from ...`）を照合する。公開entrypoint経由のexportを確認した場合は `index_export_manifest` と `index_export_public` の evidence を返す。manifestが存在しない、または条件分岐の全経路を解析できない場合は「未確認」として扱う。

## HTTP Tool

`http_head` と `http_get_document` は SSRF 対策（GET/HEAD のみ、localhost/private/link-local/metadata 拒否、redirect 再検証、allowlist、Content-Type、size、timeout）と同時に M6 で実装する。0.1.0 には存在しない。

## Agent からの利用

- Planner は catalog に存在する Tool だけを計画に含める。
- Planner は識別子・引用句・path・履歴/commit/diff などの intent に加え、日本語/英語の技術用語（認証→auth/login、設定→config など）を決定的な識別子へ展開する。翻訳サービスではなく、小さな固定辞書である。
- Retriever は ToolExecutor 経由で実行し、`EvidenceCandidate` へ正規化する。
- Reasoner は Tool を直接実行しない。追加調査は `RetrievalGap` として Orchestrator に返し、bounded replan を行う。
- 失敗した Tool は回答全体を失敗させず、warning として記録し、回答に「調査できなかった内容」を明示する。

## セキュリティ

- Tool に shell、file write、repository mutation、network fetch はない。
- repository 引数は登録済み ID のみを受け付け、未登録・scope 外は `forbidden` を返す。
- path は `RepositoryPath` で検証し、絶対 path、`..`、`.git`、backslash を拒否する。
- Tool 結果は untrusted data として model prompt 内で区切り、命令として解釈しない。
