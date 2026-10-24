# ADR-0020: M4 Agent Pipelineの実装方式

- Status: Accepted
- Date: 2026-10-25

## Context

M4はOllamaを最初のModel Providerとし、5段階Pipeline（Planner、Retriever、Evidence Ranker、Reasoner、Composer）で検証可能な根拠付き回答を返す。小規模local modelはTool CallingやJSON出力が不安定で、すべてをmodel判断に依存すると回答の再現性とcitation整合性が崩れる。またdomain層へPydanticやHTTP依存を持ち込めない。

## Decision

1. **Plannerはdeterministic heuristicを既定**とする。質問から識別子、引用句、path、履歴/commit/diff/callers/directory intentを抽出し、登録済みTool catalogだけを使ってboundedな`InvestigationPlan`を作る。`use_model_planner=true`のときだけmodel plannerを試し、失敗時はheuristicへ戻す。
2. **RetrieverはTool Executor経由のread-only実行のみ**を行い、tool結果を`EvidenceCandidate`へ正規化する。Tool callは必ずcatalog、JSON Schema、repository authorization、timeout、result上限を経由し、modelが任意toolを直接実行することはない。
3. **ReasonerだけがLLMを使う**。verified factとinferenceを分離し、citation IDは実在するevidenceだけを受理する。LLM無効・timeout・不正JSON時はdeterministic extractive reasonerへfallbackし、回答statusを`partial`にする（degraded mode）。
4. **Composerはdeterministic**にCanonicalResponseを組み立て、citationはevidenceから直接生成する。LLMにcitation本文を生成させない。これにより小規模modelでもSHA/path/lineが欠落しない。
5. **Tool入出力のPydantic modelはapplication/tools境界に置く**。domainはstdlib dataclassのまま維持し、config/infrastructureへのPydantic集約ルールに対する明示的な境界例外とする。modelへ渡すJSON Schemaは同じPydantic modelから生成し、Executorは同じmodelで再検証する。
6. M4のRetrievalはSQLite indexとRepository Portに限定し、BM25 FTS5、Qdrant vector、確定Call/Dependency GraphはM6へ送る。Tool catalogに未実装Toolを載せない。

## Consequences

- 回答のcitationとSHA整合性がmodel品質に依存しない。model停止時も根拠列挙型の回答が返る。
- Plannerの表現力はheuristicの範囲に制限されるため、複雑な多段調査はM6以降の改善対象となる。
- Pydantic境界例外をADRで明示することで、domain purityと厳密なTool validationを両立する。
