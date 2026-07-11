# Changelog

このプロジェクトは[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)の形式とSemantic Versioningを採用予定です。

## [Unreleased]

### Added

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

- secret model representationとstructured logのredaction test
- plugin allowlist、API compatibility、capability conflict validation
