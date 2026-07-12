# ADR-0019: SQLite as the M3 Source of Truth

- Status: Accepted
- Date: 2026-07-12

## Context

SQLite and a future vector store cannot share an atomic transaction.

## Decision

SQLite is the sole source of truth and publication state in M3. Store validated bounded UTF-8 text in SQLite to preserve exact search excerpts, line coordinates, and reproducibility. Unsafe, binary, secret, oversized, generated, and minified content is never stored; only safe reason codes and counts are retained.

No Qdrant writes occur in M3. A future vector projection must be derived from an already active SQLite generation and be disposable/rebuildable.

## Consequences

Queries can reproduce M3 evidence without consulting a mutable working tree. Database size and secret-detection false negatives remain risks, mitigated by M2 limits, validation, permissions, backup policy, and generation retention controls.
