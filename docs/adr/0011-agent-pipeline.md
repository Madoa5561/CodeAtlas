# ADR-0011: Agentを5つの責務へ分離

- Status: Accepted
- Date: 2026-07-11

## Decision

AgentをPlanner、Retriever、Evidence Ranker、Reasoner、Response Composerへ分離する。Orchestratorは状態、budget、再計画、cancelだけを管理する。

## Consequences

段階ごとの評価と差し替えが可能になる。一方、中間modelのversion管理とtrace量が増える。
