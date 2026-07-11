# Security Policy

## Supported Versions

CodeAtlasはpre-alphaで、security updateを保証するreleaseはまだありません。最新のdefault branchだけを調査対象とします。

## Reporting a Vulnerability

脆弱性、認証情報、private repositoryの情報を公開Issueへ投稿しないでください。GitHubリポジトリのSecurityタブからPrivate vulnerability reportingの「Report a vulnerability」を使用してください。

Private vulnerability reportingが利用できない場合は、秘密情報を含めずにGitHub Issueで「private reporting channelが必要」とだけ連絡してください。架空のemailや公開paste serviceは使用しないでください。

報告には可能な範囲で影響、再現条件、対象revision、緩和策を含めてください。受領確認や修正期限はmaintainer体制確立前のため保証できません。

## Scope

M1では設定、logging、Plugin Registry、bootstrap、Docker/CIが対象です。Repository解析、Discord、LLM、Qdrant接続は未実装であり、現在のattack surfaceではありません。

第三者pluginはsandboxではなく信頼済みcodeとして実行される設計です。未知のpluginをインストールしないでください。
