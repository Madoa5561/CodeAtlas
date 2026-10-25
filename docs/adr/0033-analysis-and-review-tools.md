# ADR-0033: 要約・影響分析・PR Review・Mermaid Tool

- Status: Accepted
- Date: 2026-10-25

## Context

M7は要約、影響分析、PR review、Mermaid生成を要求する。既存Agentはtool evidenceだけを根拠に回答するため、これらの機能は文字列生成をツール内で完結させるのではなく、検証可能なevidenceを構築し、Reasonerに説明させる必要がある。また、確定Call Graph resolverは未実装であり、既存relationはconfidence付きcandidateである。

## Decision

1. `summarize_repository`はactive generationのfile metadata、language、symbol kind、top declarationsを集計し、`analysis/summary.md`というvirtual provenance pathのevidenceとして返す。要約文はReasonerがevidenceから生成する。
2. `analyze_impact`はsymbol/fileを起点に、`raw_target`がname/qualified name/path variantに一致するincoming relationをBFSで辿り、impacted files/symbolsとrelation evidenceを返す。深さ・件数を制限する。relationは確定edgeではなくcandidateであり、confidenceとprovenanceを保持する。
3. `generate_mermaid`は既存`traverse_graph`を再利用し、nodes/edgesから`flowchart LR`を生成する。labelはMermaid構文を壊さない文字へsanitizeし、長さを制限する。diagramは`analysis/graph.mmd` evidenceとして返す。
4. `review_pull_request`はGitHub PR metadata/diff、diffから抽出したchanged files、changed file内のindexed symbols、そのsymbolsへのincoming relationを組み合わせ、`github_pr_review` evidenceとexternal URLを返す。変更の正しさを断定せず、review contextとimpactを提示する。
5. Plannerはsummary/impact/Mermaid/PR review intentを検出して専用toolを計画する。既存のget_pull_request/diffは残し、review toolは追加の統合contextとして扱う。
6. すべてのToolはrepository active indexを要求し、citationは既存のcommit/path/line provenanceかexternal URLへ戻せる。Mermaid/summaryは実ファイルではなく解析用virtual pathを使う。

## Consequences

- 要約・影響・diagram・reviewを、幻覚ではなくcitation付きevidenceに基づいて回答できる。
- relationがcandidateであるため、impact/PR reviewの網羅性は保証されない。確定Call/Dependency Graph resolverは未実装のまま残る。
- Mermaidは構文検証を最小限しか行わない。複雑なgraphはtruncateされる。
- PR reviewはGitHub tokenとactive indexを要求する。tokenなしでは安全にnot_foundとなる。
