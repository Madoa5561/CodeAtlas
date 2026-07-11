# Tool Interface 設計

## 共通契約

全 Tool は Pydantic v2 の strict input/output model、安定した name/version、timeout、result limit を持つ。例外は `not_found`、`invalid_input`、`forbidden`、`timeout`、`rate_limited`、`provider_error`、`internal_error` に分類する。

共通 context は `request_id`、`repository_id`、`commit_sha`、deadline、authorization scope を持つ。共通 result は evidence、cursor、truncation、warnings を持つ。登録済み repository ID と root containment を検証する。

## Repository Tool 群

| 分類 | Tool |
| --- | --- |
| catalog | `list_repositories`, `get_repository` |
| refs/tree | `list_branches`, `list_tags`, `list_directory`, `read_file` |
| search/symbol | `search_text`, `search_code`, `find_symbol`, `find_definition`, `find_references` |
| graph | `find_callers`, `find_callees`, `get_dependencies`, `get_dependents` |
| git | `get_commit`, `search_commits`, `get_file_history`, `get_blame`, `compare_refs`, `get_diff` |
| collaboration | `get_pull_request`, `get_pull_request_diff`, `list_issues` |

## 代表型

- `RepositoryRef`: stable repository ID
- `RevisionRef`: branch/tag/SHA の入力。結果は resolved SHA 必須
- `SourceLocation`: repository、SHA、path、start/end line、symbol ID
- `SearchQuery`: query、filter、limit、cursor
- `SearchHit`: location、excerpt、method、score、verified
- `ToolError`: category、safe message、retryable、correlation ID

## HTTP Tool と Agent 境界

`http_head` と `http_get_document` のみを公開する。URL/DNS/IP、redirect、Content-Type、size、timeout、allowlist を adapter 内で強制し、POST、任意 header、cookie、認証付き URL は許可しない。

Model 応答は `CanonicalToolCall(id, name, arguments_json)` に正規化する。Tool Executor が validation と authorization を行い、Provider Adapter は Tool を直接実行しない。

## Agent Component契約

- `PlannerPort.plan(QueryContext) -> InvestigationPlan`
- `RetrieverPort.retrieve(InvestigationPlan) -> EvidenceCandidateSet`
- `EvidenceRankerPort.rank(QueryContext, EvidenceCandidateSet) -> RankedEvidenceSet`
- `ReasonerPort.reason(QueryContext, RankedEvidenceSet) -> ReasoningResult`
- `ResponseComposerPort.compose(ReasoningResult, InteractionCapabilities) -> CanonicalResponse`

ToolはRetrieverだけがTool Executor経由で呼び出す。Reasonerの追加調査要求は`RetrievalGap`としてOrchestratorへ戻す。

## Graph Tool

`traverse_graph`、`find_change_impact`、`trace_cross_language_symbol`、`get_symbol_history`を追加する。queryはstart node、allowed edge types、direction、max depth、snapshot SHA、limitを必須化し、無制限traversalを禁止する。
