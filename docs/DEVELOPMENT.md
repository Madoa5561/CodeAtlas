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

coverageの初期閾値は80%とする。生成物は`dist/`に出る。

## Architecture Rules

- `domain`は標準libraryだけに依存する。
- Pydanticは`config`、structlogは`infrastructure`に閉じ込める。
- Plugin APIは外部serviceを自動発見・downloadしない。
- composition rootと管理CLI factoryだけが具象adapter/pluginを組み立てる。
- Qdrant、Discord、LLM、GitHub、Webhook、MCPをM3へ追加しない。

## M2 Repository Tests

Git 2.40以上を使用する。ripgrepがあればfixed-string/regex searchを使用でき、ない場合はPythonのfixed-string fallback testが動く。regex fallbackはReDoSとengine差異を避けるため安全に拒否される。fixtureは各testのOS temporary directoryにlocal Git repositoryを作り、networkを使わない。Windowsでdirectory symlink権限がない場合、そのalias testだけskipされるが、Git symlink objectの拒否は常に検証される。

## M3 Development

M3 testsはOS temporary directoryにGit repositoryとSQLite DBを作り、外部networkを使用しない。migration lifecycleを確認するには次を実行する。

```powershell
codeatlas-admin --json db upgrade
codeatlas-admin --json db current
```

schema変更はSQLAlchemy persistence model、Alembic migration、upgrade/downgrade/re-upgrade testを同じ変更で更新する。application起動時の自動migrationは禁止する。Tree-sitter/grammar更新時はconstraints、ADR-0017、parser fingerprint、fixtureを更新する。

## M4/M5 Development

M4/M5のtestsは外部serviceをmockし、networkを使用しない。Ollamaへのlive testは次で明示実行する。

```powershell
$env:CODEATLAS_TEST_OLLAMA="1"
python -m pytest tests\test_ollama_provider.py -k live
```

ローカルでDiscordを使わずにAgentを確認する。

```powershell
codeatlas-admin --json db upgrade
codeatlas-admin --json index full C:\work\linejs HEAD
$env:CODEATLAS_REPOSITORY__REGISTERED_PATHS='["C:/work/linejs"]'
codeatlas-ask --json ask "Client クラスはどこで定義されていますか？"
```

構成は`src/codeatlas/runtime.py`だけがAdapterを組み立てる。domainはstdlibのみ、applicationはdomain portのみに依存する。Tool入力のPydantic modelはapplication/tools境界に閉じ込め、modelへ渡すJSON Schemaと実行時validationを同じmodelから生成する（ADR-0020）。

テストの分類:

- `test_agent_pipeline.py` / `test_agent_security.py`: fake tool/executor/modelによるpipelineとprompt injection境界
- `test_index_query.py` / `test_tools.py`: 実SQLite indexと実Git fixtureによるretrievalとTool authorization
- `test_conversation_store.py`: 冪等claim、履歴、lock、retention
- `test_discord_*`: 純粋なpresentation/filter/conversion contract
- `test_ollama_provider.py`: httpx MockTransportによるprovider contract
- `test_runtime.py`: composition rootとdiagnostic CLI

新しいToolを追加するときは、Pydantic input model、catalog entry、handler、authorization test、docs/TOOLS.mdを同じ変更で更新する。未実装Toolをcatalogへ載せない。

## Dependency Updates

`pyproject.toml`の互換範囲と`constraints/py312.txt`を同じPull Requestで更新し、Windows/Linux CIのquality gatesとlicense/security影響を確認する。constraintsはclean Python 3.12環境で検証する。
