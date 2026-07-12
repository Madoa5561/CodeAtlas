# Repository Provider

## M2 Scope

The local provider registers non-bare Git worktrees in memory and exposes typed read-only operations for repository metadata, branches, tags, commit trees, text files, search, commits, history, blame, and diffs. GitHub API, persistence, indexing, parsers, and remote clone are not implemented.

## Identity and Registration

Input paths are expanded and strictly resolved through symlinks/junctions. Git determines the canonical top-level and common Git directory. The stable `RepositoryId` is a SHA-256-derived opaque value over the common Git directory's filesystem identity, with its canonical path as a fallback on platforms that do not expose a file identity. Equivalent path spellings and same-filesystem moves deduplicate. Copying to another filesystem is a distinct repository identity. Submodules are rejected; nested repositories are registered only when explicitly supplied.

The registry is an exchangeable Port with an in-memory M2 adapter. Only registered IDs can be queried.

## Revision and Object Reads

Branches, tags, `HEAD`, and SHAs resolve to a full 40-character commit SHA. Revision expressions such as ancestry traversal and reflog selectors are rejected. Exact branch/tag collisions are rejected. Results always retain the resolved SHA. Tree and blob reads use `ls-tree` and `cat-file`; working-tree content is never used.

## History

Commit formats are fixed NUL-delimited fields with full SHA, parents, author identity, ISO-8601 authored/committed timestamps, subject, and body. Commit search supports message, author, date, path, and result limits. File history optionally uses `--follow` rename detection.

Blame uses line porcelain against a commit SHA. Commit diff uses the first parent for merge commits. Root commits compare against Git's empty tree. Diff files are individually validated against path, attributes, binary, secret, and size policies before their patches are combined.

## Plugin

`LocalGitRepositoryPlugin` is explicitly `built_in` and provides capability `repository:local-git` version `1.0.0`. An unset allowlist permits it; `[]` rejects it; an ID list must include `codeatlas.repository.local-git`.
