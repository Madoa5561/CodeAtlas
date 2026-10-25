# ADR-0034: MCP ServerとWeb API Tool Endpoint

- Status: Accepted
- Date: 2026-10-25

## Context

M7はMCP、CLI、Web API拡張を要求する。既存のCLIとDiscordはInteractionApplicationService経由でAgent全体を実行するが、外部MCPクライアントやWeb API利用者は個々のread-only Toolを直接呼びたい場合がある。Tool catalogとToolExecutorは既に共通で、security境界もそこで強制されている。

## Decision

1. `codeatlas-mcp`を追加し、stdio上で改行区切りJSON-RPC 2.0を提供する。外部MCP SDKには依存せず、必要なinitialize/ping/tools/list/tools/callだけを小さく実装する。
2. MCP `tools/list`は既存`ToolExecutor.catalog()`からname/description/inputSchemaを返す。`tools/call`は`CanonicalToolCall`を構築し、既存のvalidation/authorization/timeout/payload上限をそのまま通す。
3. MCPのtool結果は`content`（JSON text）と`structuredContent`（ok/payload/evidence/error）を返す。失敗時もJSON-RPC errorではなく`isError=true`とsafe messageを返し、モデルが安全に解釈できるようにする。
4. Web APIへ`GET /tools`と`POST /tools/call`を追加する。`repository_id`を指定した場合は登録済みrepositoryだけへscopeし、既存ToolContextのallowlistへ渡す。
5. MCP/APIは既存のread-only catalogを共有し、write/shell/network Toolは追加しない。ログはMCPではstderrだけへ出し、stdoutをJSON-RPC専用に保つ。
6. MCPはruntimeをDiscord/API無効で起動し、同一SQLite index/GitHub/Qdrant/HTTP設定を利用する。終了時はruntime.stopでadapterを閉じる。
7. CLIは既存`codeatlas-ask`/`codeatlas-admin`を維持し、MCPを新entrypointとして追加する。CLIのAgent pipelineとTool直接実行は同じcoreを使う。

## Consequences

- MCPクライアントとローカルWeb APIから、Agentを介さず検証済みToolを利用できる。
- MCP/APIはローカル管理者境界を前提とし、認証はreverse proxy/network制御に依存する。
- JSON-RPCのbatchやresources/promptsは未実装で、tools capabilityだけを提供する。
- stdio transportはcontainer exec経由でも利用できるが、MCPクライアント設定ではwrapperが必要になる。
