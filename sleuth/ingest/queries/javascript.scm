; bare function
(function_declaration
  name: (identifier) @name.function) @definition.function

; exported function — @definition.function is the export_statement wrapper
; (so "export" text is included); @inner.function is the raw node, used
; only for dedup against the bare pattern above
(export_statement
  declaration: (function_declaration
    name: (identifier) @name.function) @inner.function) @definition.function

; method (decorators on methods are siblings in this grammar, not wrappers,
; so they never break this match — no separate decorated-method pattern needed)
(class_declaration
  name: (_) @name.class
  body: (class_body
    (method_definition
      name: (property_identifier) @name.method) @definition.method)) @definition.class_context

; whole class, bare
(class_declaration
  name: (_) @name.class) @definition.class

; exported class
(export_statement
  declaration: (class_declaration
    name: (_) @name.class) @inner.class) @definition.class
