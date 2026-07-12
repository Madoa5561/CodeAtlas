# Development

## Prerequisites

- Python 3.12以上
- Git
- Docker EngineとDocker Compose plugin（container検証時のみ）

M1のPython依存管理は[ADR-0009](adr/0009-python-dependency-management.md)に従い、標準`venv`、pip、constraintsを使用する。

## Setup

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]" -c constraints\py312.txt
```

### Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]" -c constraints/py312.txt
```

`.env.example`を参考にローカル`.env`を作成できるが、M1のtestにはsecretも外部serviceも不要である。`.env`はGit管理しない。

## Quality Gates

```powershell
python -m pytest
python -m coverage run -m pytest
python -m coverage report
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
```

coverage閾値はM1では80%とする。生成物は`dist/`に出る。

## Architecture Rules

- `domain`は標準libraryだけに依存する。
- Pydanticは`config`、structlogは`infrastructure`に閉じ込める。
- Plugin APIは外部serviceを自動発見・downloadしない。
- `bootstrap.py`だけが具象foundation serviceを組み立てる。
- M2前にRepository、Discord、LLM、Tree-sitter、Qdrant clientを追加しない。

## M2 Repository Tests

Git 2.40以上を使用する。ripgrepがあればprimary search、なくてもPython fallback testが動く。fixtureは各testのOS temporary directoryにlocal Git repositoryを作り、networkを使わない。Windowsでdirectory symlink権限がない場合、そのalias testだけskipされるが、Git symlink objectの拒否は常に検証される。

## Dependency Updates

`pyproject.toml`の互換範囲と`constraints/py312.txt`を同じPull Requestで更新し、Windows/Linux CIのquality gatesとlicense/security影響を確認する。constraintsはclean Python 3.12環境で検証する。
