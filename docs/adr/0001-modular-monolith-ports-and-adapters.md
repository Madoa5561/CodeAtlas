# ADR-0001: Modular Monolith と Ports and Adapters

- Status: Accepted
- Date: 2026-07-11

## Context

16の必須モジュールがあるが、初期から分散化すると整合性、運用、テストの負担が大きい。

## Decision

単一 deployable とし Domain/Application/Adapter/Entrypoint を分離する。Indexer worker は同じ application module を使って後から分離可能にする。

## Consequences

局所 transaction と開発体験を保てる一方、import rule で境界を守る必要がある。
