# ADR-0012: 横断Unified Knowledge Graph

- Status: Accepted
- Date: 2026-07-11

## Decision

TypeScript、Thrift、Git History、Call Graph、Dependency Graphをsnapshot-scoped identityとprovenanceを持つ共通graphへ統合する。

## Consequences

cross-language/change-impact queryが可能になる。一方、差分更新とheuristic edgeの品質管理が複雑になる。
