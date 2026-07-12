# Parser Architecture

`SourceParserPort` is a domain Protocol with parser identity, versions, supported languages/extensions, selection, and parse output. Tree-sitter types remain in the adapter. The built-in parser plugin declares capability `parser:typescript-javascript` and follows the existing allowlist three-state policy.

M3 extracts classes, interfaces, functions, methods, constructors, type aliases, enums, variables, namespaces, anonymous functions, imports, exports, call candidates, extends, and implements candidates. Relations remain unresolved text with source span, confidence, and future resolver metadata.

Tree-sitter byte offsets are preserved. Lines are 1-based. Columns are converted from UTF-8 byte columns to Unicode character columns. CRLF and LF are handled by Tree-sitter points without newline rewriting.

Parser comments and source text are untrusted data, not instructions. AST node count, traversal depth, source size, signature length, target length, and syntax-error span count are bounded.
