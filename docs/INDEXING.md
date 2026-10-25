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

## Read-only Query

`SqliteIndexQuery` exposes the active generation to M4 tools without mutating state. Each connection sets `query_only=ON`. It supports active snapshot lookup, file/content reads, symbol search by exact name, qualified-name suffix, kind, and path prefix, bounded text search with 1-based line and 0-based column excerpts, and relation queries by kind, target, or source symbol. All queries return full commit SHAs and are bounded by `RetrievalLimits`. Missing active generations produce a safe `not_found` tool outcome rather than falling back to working-tree content.

## Lexical Projection (M6)

`code_search_fts` is a line-level SQLite FTS5 projection of safe file content. It is generation-scoped and rebuildable from `file_versions.content_utf8`; it is not the source of truth.

- indexing inserts non-empty lines during `add_file_bundle`
- incremental reuse copies FTS rows from the previous generation
- failed generations delete their FTS rows
- migration `0003_m6_fts` creates the table and backfills existing databases
- `search_lexical` converts an untrusted query to a bounded quoted MATCH expression and ranks with BM25
- Japanese text is constrained by the `unicode61` tokenizer, so literal search and symbol search remain part of hybrid retrieval

Graph traversal is implemented over the same relation candidates. `traverse_graph` starts from a symbol or file and performs bounded BFS with edge-kind, direction, depth, and result caps. Relation candidates retain provenance and confidence; they are not final resolved call edges.
