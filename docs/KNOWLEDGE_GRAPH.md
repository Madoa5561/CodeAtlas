# Unified Knowledge Graph

## 目的

TypeScript、Thrift、Git History、Call Graph、Dependency Graphを同一snapshot上で横断し、すべての回答根拠を不変なcommit SHAとsource locationへ戻せるようにする。

## Identity

Node IDは`repository_id + commit_sha + kind + stable_key`から導出する。symbolのstable keyはlanguage、qualified name、signature、file identityを材料とし、rename trackingは同一性を上書きせず`RENAMED_FROM`として表現する。

## Node Families

| Domain | Node |
| --- | --- |
| Repository | Repository、Snapshot、Branch、Tag、Directory、File |
| TypeScript | Module、Namespace、Class、Interface、Function、Method、Type、Enum、Variable、Import、Export |
| Thrift | Document、Namespace、Typedef、Const、Enum、Struct、Union、Exception、Service、Method、Field |
| Git History | Commit、Author、Change、PullRequest、Issue |
| Dependency | Package、Module、ExternalDependency、BuildTarget |

## Edge Families

| View | Edge |
| --- | --- |
| Structure | `CONTAINS`, `DEFINES`, `DECLARES`, `MEMBER_OF` |
| Language | `IMPORTS`, `EXPORTS`, `REFERENCES`, `IMPLEMENTS`, `EXTENDS`, `USES_TYPE` |
| Call Graph | `CALLS`, `MAY_CALL`, `OVERRIDES`, `DISPATCHES_TO` |
| Thrift | `INCLUDES`, `RETURNS`, `ACCEPTS`, `THROWS`, `FIELD_TYPE` |
| Cross-language | `GENERATED_FROM`, `BINDS_TO`, `IMPLEMENTS_SERVICE`, `SERIALIZES_AS` |
| Dependency | `DEPENDS_ON`, `DEV_DEPENDS_ON`, `RESOLVES_TO` |
| Git History | `INTRODUCED_BY`, `MODIFIED_BY`, `DELETED_BY`, `RENAMED_FROM`, `PARENT_OF`, `TOUCHES` |

## 横断クエリ例

- Thrift service -> `GENERATED_FROM`/`IMPLEMENTS_SERVICE` -> TypeScript client -> `CALLS` -> consumer method -> `MODIFIED_BY` -> commit/PR
- npm package -> `DEPENDS_ON` -> module -> `IMPORTS` -> symbol -> callers -> owning directory
- commit -> changes -> symbols -> call/dependency neighborhood -> 変更影響候補

## Provenance

Node/Edgeは`extractor_id`、plugin version、source location、snapshot、confidence、derivation kindを持つ。`verified`、`static_inference`、`heuristic`、`model_inference`を区別し、model inferenceをverified edgeとして保存しない。

## Storage Ports

- `GraphMutationPort`: snapshot generationへのbatch upsert/tombstone
- `GraphQueryPort`: typed traversal、bounded depth、filter、pagination
- `GraphProjectionPort`: call/dependency/history等のprojection構築
- `GraphConsistencyPort`: dangling edge、snapshot混在、provenance欠落を検査

初期SQLite実装はadjacency tableとtyped property tableを使用する。専用graph DBへ移行してもDomain query specificationは維持する。

## 更新と整合性

Parser出力はstaging generationへ書き、すべてのedge endpointが同じsnapshotまたは明示的history edgeであることを検証する。差分更新では影響するfile、symbol、incoming/outgoing edge、projectionを再計算する。
