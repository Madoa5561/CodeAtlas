# Index Database

## Schema

| Table | Purpose |
| --- | --- |
| `repositories` | Stable repository IDs only; no absolute path |
| `repository_snapshots` | Commit, generation, parser and policy fingerprints |
| `index_generations` | Lifecycle, generation number, safe failure and statistics |
| `indexed_files` | Generation-scoped logical file metadata and security status |
| `file_versions` | Commit/object/content identity and optional safe UTF-8 bytes |
| `symbols` | Parser-derived declaration and source span |
| `import_relations` | Unresolved import targets |
| `export_relations` | Export evidence |
| `reference_relations` | Call/extends/implements/reference candidates |
| `index_jobs` | Repository lease owner token and expiry |
| `validation_results` | Persisted pre-publication result |
| `schema_metadata` | Storage policy marker |

Foreign keys are enabled. Cascades are limited to generation-owned derived rows. Repository deletion is restricted. `(repository_id, generation_number)`, `(generation_id, path)`, and `(generation_id, symbol_id)` are unique. A SQLite partial unique index enforces one active generation per repository.

Timestamps are UTC ISO-8601 strings because SQLite has no timezone-aware timestamp type. Status values are string enums guarded by a check constraint for migration portability.

## Migration

```powershell
codeatlas-admin db current --json
codeatlas-admin db upgrade --json
```

Downgrade is explicit and destructive to M3 index data:

```powershell
codeatlas-admin db downgrade --revision base --json
```

Back up the SQLite file and stop writers before downgrade. Application startup does not migrate automatically and fails if the Alembic revision is not current.
