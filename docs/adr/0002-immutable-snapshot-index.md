# ADR-0002: immutable snapshot index

- Status: Accepted
- Date: 2026-07-11

## Context

行番号は解析時 SHA に対応し、SQLite と Qdrant の部分更新を公開できない。

## Decision

索引を repository + commit SHA + generation で識別し、staging の検証後に active pointer を atomic に切り替える。Qdrant は派生データとする。

## Consequences

再現性と rollback を得る代わりに一時容量と garbage collection が必要になる。
