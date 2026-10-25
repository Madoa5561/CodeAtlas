# ADR-0030: Thrift ParserとCross-Language Relation候補

- Status: Accepted
- Date: 2026-10-25

## Context

M7はThriftのnamespace、include、typedef、const、enum、struct、union、exception、service、extends、method、arguments、throws、field ID、required/optionalを解析し、ThriftからTypeScript側への参照関係を可能な範囲でグラフ化することを要求する。プロジェクトにThrift Tree-sitter grammarの依存はなく、外部parserを追加するとライセンス・grammar version・CIの管理コストが増える。

## Decision

1. Thrift専用のbounded recursive-descent parserを`adapters/parsing/thrift_parser.py`に実装する。Tree-sitter Nodeや外部grammarへ依存しない。
2. 字句解析はコメント、文字列、数値、識別子、区切りを扱い、byte/line/column spanを保持する。token数とsource byte数を上限で制限し、未知文字はsyntax errorとして記録して継続する。
3. 宣言は`SymbolKind` extension（SERVICE/STRUCT/UNION/EXCEPTION/FIELD/TYPEDEF/CONST）で既存symbol storeへ保存する。methodはserviceのchild、argument/throws/fieldはmethod/aggregateのchildとし、field IDとrequired/optionalをsignatureへ含める。
4. includeはImportRelation、extendsはReferenceRelation(kind=extends)とする。field/methodの型名はReferenceRelation(kind=reference)として記録し、generated TypeScriptの型名と名前一致でgraph traversalできるようにする。
5. 追加でserviceごとに`{Service}Client`へのReferenceRelationをconfidence=0.5、resolver_metadata=`thrift-generated-client-heuristic`で記録する。これはgenerated client命名のheuristicであり、確定resolverではない。
6. Thrift parserはbuilt-in parser pluginとして登録し、indexing CLIとWebhook indexingのparser tupleへ追加する。`.thrift`は`language=thrift`として扱う。
7. cross-language matchはname-basedであり、namespace/module境界やgenerated code規約を完全には解決しない。M7の確定Call/Dependency Graph resolverで置き換え・強化する。

## Consequences

- 外部grammar依存なしでThrift宣言と代表的relationをindex/graphへ統合できる。
- generated-client linkは誤検出し得るため、confidenceとresolver_metadataを保持し、citationのverified扱いと分離する。
- Thrift文法の全機能（annotations、advanced types、constants expressions）はsubsetであり、未対応構文はsyntax errorとして安全に記録される。
- parserは宣言の意味解決（型解決、namespace解決）を行わない。それは後続resolverの責務である。
