# ADR-0027: GitHub外部evidenceとCitation

- Status: Accepted
- Date: 2026-10-25

## Context

M6はGitHub PR/Issue Toolを要求する。既存のEvidenceCandidate/Citationはrepository pathとcommit SHAで検証可能性を表すが、GitHub PR/Issueはrepository外の文書であり、PR head SHAやissue番号で参照される。回答モデルにはpayloadではなくevidenceだけが渡るため、PR/Issue本文をevidenceとして渡しつつ、citationに外部URLを残す必要がある。

## Decision

1. GitHub read-only adapterは`GitHubReadOnlyPort`としてdomainに置き、REST/httpxの詳細をapplicationへ漏らさない。response size、body、diff、issue件数、retryをboundedにする。
2. EvidenceKindへ`PULL_REQUEST`/`ISSUE`を追加し、EvidenceCandidate/Citationへ任意の`external_url`を追加する。commit SHAとpathは既存のcitation表示・検証モデルを維持するため必須のままとする。
3. PRはGitHubが返すhead SHAをcommit SHAとして使い、仮想path `github/{owner}/{repo}/pull/{number}.md` を持つ。Issueはactive index snapshotのSHAを文脈SHAとして使い、仮想path `github/{owner}/{repo}/issues/{number}.md` を持つ。どちらもcitationに実URLを付ける。
4. 仮想pathはrepository内実ファイルではない。citation表示はexternal_urlを併記し、ユーザーがGitHub上で検証できるようにする。
5. GitHub toolsはlocal repositoryのactive indexを要求し、`repository_id`でcitationを既存のrepositoryへ紐づける。GitHub slugはtool引数または`github.default_repository`から解決する。
6. PR diffは最大200k文字、本文は20k文字、API responseは4MB、issueは最大30件に制限する。tokenはログ・例外・payloadに含めない。

## Consequences

- PR/Issue本文をAgentの回答根拠にでき、Discord/CLI/APIで外部URLを提示できる。
- Issueのcommit SHAは厳密にはissueのrevisionではないため、外部文書であることをURLとkindで明示する。将来はexternal document専用のcitation型へ分離する可能性がある。
- GitHub token未設定時はtoolsが`not_found`として安全に失敗する。公開repositoryでもtoken設定を要求する。
- 自動index更新は未実装で、Webhookは受信・冪等記録のみである。
