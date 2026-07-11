# ADR-0013: Plugin、Event、UI非依存Interaction

- Status: Accepted
- Date: 2026-07-11

## Decision

拡張をcapability-based Plugin APIで登録し、Domain/Application/Integration Eventをtransactional outboxで配送する。UIはCanonical Interaction/Response Portへ接続するpluginとする。

## Consequences

Discord以外をcore変更なしで追加できる。Pluginはsecurity sandboxではなく、event consumerには冪等性が必要になる。
