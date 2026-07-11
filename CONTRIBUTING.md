# Contributing to CodeAtlas

CodeAtlasはpre-alphaです。IssueやPull Requestを作成する前に[ロードマップ](docs/ROADMAP.md)と[アーキテクチャ](docs/ARCHITECTURE.md)を確認してください。

## Reporting

- 再現可能なbug、設計提案、文書改善はGitHub Issuesを使用してください。
- 脆弱性や秘密情報はIssueへ投稿せず、[SECURITY.md](SECURITY.md)に従ってください。
- 大きな変更は実装前にIssueでscopeとADR要否を合意してください。

## Development

Python 3.12と標準`venv`を使用します。詳細は[DEVELOPMENT.md](docs/DEVELOPMENT.md)を参照してください。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -c constraints\py312.txt
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

## Pull Requests

- 変更を小さく保ち、M2以降の機能をM1修正へ混在させないでください。
- public APIにdocstringと型を追加してください。
- behavior変更にはテストを追加してください。
- dependency変更は理由、license、security影響を説明し、constraintsを更新してください。
- 設計判断を変更する場合はADRを追加またはsupersedeしてください。

Contributionを送信すると、そのContributionをApache License 2.0の下で提供することに同意したものと扱われます。
