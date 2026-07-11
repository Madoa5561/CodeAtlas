# ADR-0009: venv、pip、constraintsによる依存管理

- Status: Accepted
- Date: 2026-07-11

## Context

Windows、Linux、GitHub Actionsで同じPython 3.12依存を再現し、利用者へ特定の外部package managerを強制せず、runtimeとdevelopment依存を分離する必要がある。

## Decision

標準`venv`とpipを採用する。直接runtime依存は`project.dependencies`、development依存は`project.optional-dependencies.dev`へ宣言し、`constraints/py312.txt`でCIと開発の推移依存を含む解決versionとbuild tool versionを固定する。

```text
python -m venv .venv
python -m pip install -e ".[dev]" -c constraints/py312.txt
```

lock更新は専用のclean Python 3.12環境で互換範囲を評価して行い、Dependabot等の自動更新はCI成功を必須とする。

## Consequences

標準ツールだけで各OSから利用でき、runtime/devの意図が明確になる。一方、constraintsはplatform固有artifactを固定せずhashも保持しないため、完全なsupply-chain lockではない。hash付きlockやSBOMは公開beta前に再評価する。
