# ADR-0014: SHA-pinned Temporary Search Snapshot

- Status: Accepted
- Date: 2026-07-11

## Context

ripgrep searches filesystem content, while CodeAtlas must ignore working-tree changes and search one immutable commit. A Git worktree would expose `.git` metadata and add locking/cleanup complexity.

## Decision

Enumerate the commit tree with `ls-tree`, read eligible blobs by object ID with `cat-file`, and materialize only validated text into an unpredictable OS temporary directory. Symlinks, submodules, excluded paths, binary content, secret candidates, and oversized files are never materialized.

Use ripgrep with JSON output, no config, argv arguments, timeout, bounded streams, minimal environment, and fixed snapshot root. Convert ripgrep UTF-8 byte offsets to public character offsets. If ripgrep is unavailable, the Python standard library provides fixed-string, case, glob, and context behavior over the same snapshot. Regex search fails safely rather than exposing the event loop to unbounded backtracking or pretending that Python and ripgrep regex dialects are identical.

The snapshot directory and files use owner-restricted permissions where supported. Before writing, normalize a path identity with Unicode NFC and the host's case rules; reject collisions rather than overwriting evidence. `TemporaryDirectory` performs cleanup on success, error, timeout, or cancellation, and cleanup failures are translated to a safe error.

## Consequences

Search evidence is tied to a commit SHA and independent of uncommitted files. Snapshot creation costs I/O and temporary disk space, bounded by repository/file policies. Lockfiles remain readable/searchable but no relevance ranking is introduced until later retrieval milestones.
