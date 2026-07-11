# ADR-0004: Hybrid Retrieval

- Status: Accepted
- Date: 2026-07-11

## Context

コード質問は完全一致、構造、履歴、意味類似で最適な検索が異なる。

## Decision

lexical/symbol/graph/git/vector を独立 Retriever とし、候補を Evidence へ正規化して application layer で融合する。

## Consequences

精度、説明可能性、縮退動作を得るが、評価 dataset と score calibration が必要になる。
