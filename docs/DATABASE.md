# Database

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
| `conversations` | Conversation scope metadata and last activity |
| `conversation_messages` | Bounded user/assistant turns for memory |
| `interaction_records` | Idempotency ledger and replayable terminal responses |
| `conversation_locks` | Per-conversation lease for serialized replies |
| `schema_metadata` | Storage policy marker |

Foreign keys are enabled. Cascades are limited to generation-owned derived rows. Repository deletion is restricted. `(repository_id, generation_number)`, `(generation_id, path)`, and `(generation_id, symbol_id)` are unique. A SQLite partial unique index enforces one active generation per repository. Conversation messages and locks cascade from their conversation; interaction records keep their own row until retention purge.

Timestamps are UTC ISO-8601 strings because SQLite has no timezone-aware timestamp type. Status values are string enums guarded by a check constraint for migration portability.

## Revisions

| Revision | Content |
| --- | --- |
| `0001_m3_transactional_index` | M3 index schema |
| `0002_m4_conversation` | M5 conversation, idempotency, lock schema（M3 dataは保持） |

## Migration

```powershell
codeatlas-admin db current --json
codeatlas-admin db upgrade --json
```

Downgrade is explicit and destructive to conversation state only when downgrading below `0002_m4_conversation`:

```powershell
codeatlas-admin db downgrade --revision 0001_m3_transactional_index --json
```

Back up the SQLite file and stop writers before downgrade. Application startup does not migrate automatically and fails if the Alembic revision is not current.

## Conversation Retention

```powershell
codeatlas-admin --json conversations purge --days 30
```

`--days`省略時は`CODEATLAS_CONVERSATION__RETENTION_DAYS`を使う。last activityがcutoffより古いconversationを削除し、messages、interaction records、locksをcascadeで除去する。返却値は削除したconversation数であり、内容はlogへ出さない。
