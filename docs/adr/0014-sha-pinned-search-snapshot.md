# ADR-0014: SHA-pinned Temporary Search Snapshot

- Status: Accepted
- Date: 2026-07-11

## Context

ripgrep searches filesystem content, while CodeAtlas must ignore working-tree changes and search one immutable commit. A Git worktree would expose `.git` metadata and add locking/cleanup complexity.

## Decision

Enumerate the commit tree with `ls-tree`, read eligible blobs by object ID with `cat-file`, and materialize only validated text into an unpredictable OS temporary directory. Symlinks, submodules, excluded paths, binary content, secret candidates, and oversized files are never materialized.

Use ripgrep with JSON output, no config, argv arguments, timeout, bounded streams, minimal environment, and fixed snapshot root. If ripgrep is unavailable, run the same fixed/regex/case/glob/context semantics with the Python standard library over that snapshot. `TemporaryDirectory` guarantees cleanup on success, error, timeout, or cancellation.

## Consequences

Search evidence is tied to a commit SHA and independent of uncommitted files. Snapshot creation costs I/O and temporary disk space, bounded by repository/file policies. Lockfiles remain readable/searchable but no relevance ranking is introduced until later retrieval milestones.
