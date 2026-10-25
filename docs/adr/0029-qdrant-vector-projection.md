# ADR-0029: Qdrant Vector ProjectionとEmbedding Provider

- Status: Accepted
- Date: 2026-10-25

## Context

M6はQdrant、Embedding Provider、Vector Search、fusion、freshness benchmarkを要求する。SQLiteはsource of truthであり、vectorは再構築可能なprojectionでなければならない。外部APIやQdrantが停止してもcode retrievalは継続できる必要がある。またCIには外部サービスやAPI keyを要求しない。

## Decision

1. Vector projectionは`VectorPoint`としてrepository/generation/commit/path/line/excerptを持つ。Qdrantのfilterで`repository_id`と`generation_id`を必須にし、別世代の検索結果を混入させない。
2. Embedding providerは`EmbeddingPort`とし、OpenAI-compatible HTTP providerを実装する。CIとoffline demo用にdeterministicな`local` hashed embeddingを用意する。`local`は意味検索ではなく、配線・テスト・再現用である。
3. QdrantはHTTP RESTで接続し、collection作成、upsert、search、count、generation deleteをboundedに実装する。Qdrant client libraryは追加しない。
4. `VectorProjector`はSQLite active generationのsafe contentを40行chunk（8行overlap、最大50 chunk/file）に分割してembedし、UUID5で決定的なpoint IDを付ける。再構築時は同一generationの既存pointを削除してからupsertする。
5. Vector projectionはindex transactionに含めない。`codeatlas-admin vector rebuild <repository>`で明示的に構築し、`vector status`でactive generationのpoints件数とcount latencyを返す。freshnessは「active generationのpoints > 0」で判定する。
6. `search_vector`はqueryをembedし、active generation filter付きでQdrantを検索して`vector_search` evidenceを返す。embedding/Qdrantが無効なら安全にnot_foundとなる。Plannerはlexical/literalと併用して計画する。
7. QdrantはComposeの`qdrant` profileで任意起動する。既定では無効で、SQLite/FTS5 retrievalは影響を受けない。

## Consequences

- M6のvector retrievalを追加サービスなしの既定構成から分離でき、Qdrant停止時も既存retrievalが動作する。
- Projectionはindex更新と自動同期しないため、freshnessは運用操作に依存する。将来はindex完了後のprojection jobをqueue化する。
- Local hashed embeddingは意味品質を持たない。本番はOpenAI-compatible endpointとQdrantを有効化する。
- Chunk境界は固定行数であり、意味境界やsymbol境界ではない。reranking/fusionの品質評価は今後の課題である。
