# ADR-0005: deny-by-default の読み取り専用境界

- Status: Accepted
- Date: 2026-07-11

## Context

Repository と Web は Prompt Injection を含み得る。

## Decision

公開 Tool は allowlist の読み取り操作だけとし shell、write、push、delete を持たせない。path/network/secret policy は deterministic code で強制する。

## Consequences

被害範囲を限定できる。将来の書き込み機能には管理者承認と新 ADR が必要になる。
