# ADR-0016: Transactional Index Generations

- Status: Accepted
- Date: 2026-07-12

## Context

An interrupted index build must not corrupt the currently queryable index.

## Decision

Extend ADR-0002 with immutable SQLite generations. New rows belong to a pending/running/validating staging generation. File bundles are committed in short transactions. Validation results are persisted before publication. A single SQLite transaction changes the prior active generation to `superseded` and the validated generation to `active`; a partial unique index permits at most one active generation per repository.

Failure or cancellation marks only staging as `failed`. Superseded generations remain available for explicit atomic rollback. Garbage collection is a separate future administrative operation.

## Consequences

Readers always observe a complete generation, while staging temporarily increases storage. SQLite is the only publication boundary in M3; Qdrant is not written or published.
