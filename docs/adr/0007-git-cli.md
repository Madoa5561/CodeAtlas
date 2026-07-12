# ADR-0007: Read-only Git CLI Adapter

- Status: Accepted
- Date: 2026-07-11

## Context

M2 requires exact Git revision, tree, history, blame, and diff behavior without exposing an arbitrary command runner. GitPython adds an abstraction layer and does not cover every porcelain/plumbing detail consistently.

## Decision

Use the configured `git` or `git.exe` executable through `asyncio.create_subprocess_exec`. `SafeGitRunner` accepts only an enum of read-only subcommands: `rev-parse`, `show`, `ls-tree`, `for-each-ref`, `log`, `blame`, `diff`, `cat-file`, and `check-attr`.

`check-attr` is added beyond the original minimum to evaluate the commit-pinned `.gitattributes` `diff` attribute before returning file/search/diff content.

The runner always uses argv arrays, disabled stdin, timeout, streaming stdout/stderr limits, minimal environment, literal pathspecs, no pager, no optional locks, no external diff, no global/system config, and no terminal prompts. Return codes and stderr become internal causes of safe CodeAtlas errors; stderr is never a public message.

## Consequences

Behavior follows installed Git closely and supports worktrees, blame, rename detection, and revision peeling. Git 2.40 or newer is required for `check-attr --source=<tree-ish>`. The adapter cannot execute caller-selected subcommands, shell strings, aliases, hooks, or write operations.
