# Transactional Indexing

## Scope

M3 indexes a registered local repository at one resolved commit SHA into SQLite. It stores safe text, file versions, TypeScript/JavaScript symbols, imports, exports, call candidates, extends, and implements candidates. Qdrant, semantic resolution, Call Graph finalization, Thrift, LLMs, and remote repository synchronization are not implemented.

## Generation Lifecycle

`pending -> running -> validating -> active -> superseded` is the success path. Pending, running, or validating may transition to failed. Superseded may become active through rollback. Other transitions are rejected by domain policy.

The indexer keeps the existing active generation while staging. Validation and its result precede one atomic transaction that supersedes the old active generation and activates the new one. Cancellation and unexpected failure mark staging failed and release the repository lease.

## File Policy

The M2 Repository Port supplies the commit tree and validated file content. Working-tree content is never read. Secret, binary, oversized, and unsafe files are represented by reason codes without content. Generated (`*.gen.ts`, `*.generated.*`) and minified (`*.min.js`, `*.min.mjs`) files are excluded by default.

Safe UTF-8 text is stored in SQLite under the M2 file limit. This is basic leak prevention, not complete DLP.

## Failure Policy

- Secret, binary, oversized, generated, minified: skip.
- Tree-sitter syntax error: index with warning count.
- Parser exception or limit: file failure without stored content.
- Excessive file failure rate: generation failure.
- DB, identity, transaction, or validation failure: generation failure.

## Incremental Indexing

The previous active generation is compared by normalized path and Git object ID. Unchanged bundles are copied into staging with commit-bound symbol IDs re-derived. Added and modified files are reread and reparsed. Deleted files are absent. Parser or policy fingerprint changes disable reuse.
