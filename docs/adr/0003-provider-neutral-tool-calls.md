# ADR-0003: Provider 非依存 Tool Call

- Status: Accepted
- Date: 2026-07-11

## Context

Model Provider ごとに Tool Calling の schema と streaming が異なる。

## Decision

Provider Adapter は canonical event へ変換する。validation、authorization、timeout、実行は Tool Executor が担う。

## Consequences

Provider 追加が容易になるが、固有機能は capability metadata で補う必要がある。
