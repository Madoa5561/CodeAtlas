# Changelog

このプロジェクトは[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)の形式とSemantic Versioningを採用予定です。

## [Unreleased]

### Added

- M3 SQLAlchemy/Alembic/SQLite schemaと明示migration CLI
- immutable staging generation、atomic active切替、rollback、lease/stale recovery
- safe UTF-8本文、file version、symbol、import/export/reference relation永続化
- built-in TypeScript/JavaScript Tree-sitter Parser Plugin
- full/incremental indexingとparser/policy fingerprintによるunchanged reuse
- M3 indexing/database/parser/recovery文書とADR-0015からADR-0019
- M2 local Git Repository Port、Application Service、in-memory Registry、built-in Plugin
- SHA固定tree/file/search/commit/history/blame/diff操作
- bounded read-only Git CLI runnerとripgrep/Python snapshot search
- repository path、binary、basic credential、resource limit security policy
- Apache License 2.0とOSS governance文書
- Python 3.12 package metadata、runtime/development依存の分離、constraints
- Pydantic Settingsによる検証済み設定
- structlogによるJSON/console loggingとsecret redaction
- 安全な共通例外体系
- Plugin Manifest、Capability、Registry、lifecycle rollback
- Composition Rootとgraceful shutdown foundation process
- pytest、Ruff、mypy、coverage、package buildのGitHub Actions
- 非root Docker imageとQdrantを任意profileにしたDocker Compose

### Security

- staging失敗・cancel時の旧active generation維持
- unsafe/secret/binary contentのSQLite非保存とactivation前validation
- repository単位のprocess内lockとSQLite lease
- secret model representationとstructured logのredaction test
- plugin allowlist、API compatibility、capability conflict validation
