# ADR-0032: SSRF対策付きSafe HTTP Tool

- Status: Accepted
- Date: 2026-10-25

## Context

M7はGET/HEADのみのHTTP Toolを要求し、localhost、private、link-local、metadata endpoint、redirect先を含むSSRF対策、timeout、response size、allowed domain、Content-Type検証、HTML本文抽出、robots.txt尊重を求める。Toolはモデルが生成したURLを扱うため、URLを信頼してはならない。

## Decision

1. HTTP ToolはGET/HEADだけを許可し、`http`/`https`のみを受け付ける。URLにcredentialsを含む場合、IP literal、localhost/`.local`/`.internal`等、およびport 80/443以外を拒否する。
2. allowlistは設定`security.http.allowed_domains`で明示し、host完全一致またはsubdomainだけを許可する。HTTP Tool有効時にallowlistが空なら起動时validationで拒否する。
3. ホスト名はDNS解決し、解決された**全ての**IPがglobal/非private/非loopback/非link-local/非multicast/非reservedであることを検証する。metadata IP 169.254.169.254はlink-localとして拒否する。DNS rebindingの完全防止はsocket pinningが必要なため、現段階では各redirectで再検証するbest-effortとする。
4. httpxの自動redirectを無効化し、301/302/303/307/308を手動で最大`max_redirects`回まで追跡する。各Locationはurljoin後に再度scheme/host/domain/DNS/port検証する。
5. responseはstreamで`max_response_bytes+1`まで読み、超過時はtruncateする。Content-Typeはtext/html、xhtml、text/plain、text/markdown、application/jsonだけを許可する。
6. robots.txtは対象hostごとに取得・キャッシュし、User-Agent `CodeAtlas/...` に対してDisallowなら拒否する。robots.txt取得失敗はfail-open（取得不可なら本文アクセスを許可）とし、その旨をADRで明示する。
7. HTMLはstdlib HTMLParserでscript/style等を除去し、titleと可視本文を抽出する。結果は`http_fetch` evidenceとしてexternal_url付きcitationへ接続する。
8. Toolは既存repositoryのactive generationをevidence provenanceとして要求し、virtual path `web/{host}/{hash}.md` を使う。

## Consequences

- モデル生成URLからの代表的なSSRF（内部IP、metadata、localhost、redirect、DNS解決先）を拒否できる。
- allowlist運用が必要で、任意のWeb検索はできない。robots.txt/利用規約の最終判断は運用者に残る。
- DNS rebindingは完全には排除できない。将来は検証済みIPへの接続pinningを実装する。
- 外部本文はcitation URL付きで提示されるが、内容の正確性はサイト依存である。
