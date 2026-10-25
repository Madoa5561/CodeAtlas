# ADR-0024: Discord回答の単一描画とexport確認

- Status: Accepted
- Date: 2026-10-25

## Context

M5のDiscord回答はcomposerのblock列とDiscord presentationのcitation/metadata footerを併用していた。citationが二重表示され、confidence・不明点・検索方法も本文とfooterで重複した。また回答全体が1message長にtrimされ、根拠一覧が削られて読みにくかった。さらに「公開APIとしてexportされているか」を確認するToolがなく、定義箇所は答えられても公開entrypointからの再exportを確認できなかった。

## Decision

1. CanonicalResponseのblock列を唯一の人間向け描画ソースとする。Confidence、不明点、引用、検索方法、追加調査候補はcomposerが一度だけblock化し、Discord presentationはblockをmarkdownへ変換して分割するだけにする。footer関数は後方互換のため残すが`render_markdown`からは呼ばない。
2. Composerのcitation選択は、reasoningが実際に引用したevidenceを優先し、未引用の検索候補は最大2〜3件に制限する。上限は8件。citation行は`path:start-end (symbol / method)`のコンパクト形式とし、repository@SHAは引用元headerへ一度だけ表示する。
3. Composerのbudgetは単一チャネルmessage長ではなく`AgentLimits.max_response_chars`を使う。Discord adapterがcapabilityに合わせてcode fence安全に複数messageへ分割する。
4. `find_exports` Toolを追加する。直接の`exported` symbolとexport relationに加え、`package.json`/`deno.json`の`exports`マップ、entrypointの`export { Name } from`再exportをboundedに照合し、`index_export_manifest`/`index_export_public` evidenceを返す。manifestが無い、または解析できない条件分岐は未確認として残す。
5. export intent（export/エクスポート/公開/entrypoint）をPlannerに追加し、`find_exports`を優先計画に含める。

## Consequences

- Discord/CLIの回答構造が一致し、引用の重複と過剰引用が解消される。
- 長い根拠付き回答が複数messageになるため、1message完結より情報量を優先する。
- 公開exportの確認はmanifest再exportに依存する。複雑なconditional exportsやbundler固有解決は未対応であり、その場合は「未確認」と表示する。
