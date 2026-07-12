# ADR-0018: Incremental File Identity

- Status: Accepted
- Date: 2026-07-12

## Context

Incremental generations need deterministic reuse without mutating an old generation or conflating renames.

## Decision

A logical file ID hashes repository ID and normalized POSIX path. A file version adds commit SHA, Git object ID, and validated content hash. A rename is delete plus add with a new logical ID. Symbol IDs include repository, commit, language, path, kind, qualified name, signature, and byte span.

When path and Git object ID are unchanged and parser/policy fingerprints match, copy the safe file bundle into the new generation. Re-derive commit-bound symbol IDs and remap parent/relation endpoints. Modified and added files are reread through the M2 Port; deleted files are absent.

## Consequences

Incremental and full generations have equivalent current-state content. Rename history and `RENAMED_FROM` remain future work.
