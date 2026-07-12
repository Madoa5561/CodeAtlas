# ADR-0017: Tree-sitter Grammar Version Management

- Status: Accepted
- Date: 2026-07-12

## Context

Parser output and symbol identity must be reproducible across installations and incremental runs.

## Decision

Use `tree-sitter` 0.24.0, `tree-sitter-typescript` 0.23.2, and `tree-sitter-javascript` 0.23.1. The packages support Python 3.12; the grammar packages declare MIT licenses, and the binding publishes the MIT classifier and a LICENSE file. Constraints pin all versions.

Persist parser ID/version and grammar version on parsed files and symbols. Include these versions in the generation parser fingerprint. A version change disables incremental AST reuse. Bound source bytes, AST node count, and traversal depth. Syntax errors are recorded; parser exceptions are file failures.

## Consequences

TypeScript, TSX, JavaScript, JSX, MJS, and CJS are supported in M3. Thrift and semantic symbol resolution are not implemented. Grammar upgrades require constraint, ADR, fixture, and compatibility review.
