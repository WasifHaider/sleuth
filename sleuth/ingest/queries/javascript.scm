; bare function
(function_declaration
  name: (identifier) @name.function) @definition.function

; exported function — @definition.function is the export_statement wrapper
; (so "export" text is included); @inner.function is the raw node, used
; only for dedup against the bare pattern above which also matches it independently
(export_statement
  declaration: (function_declaration
    name: (identifier) @name.function) @inner.function) @definition.function

; arrow-function / function-expression assigned to a variable — the extremely
; common "const Foo = () => {...}" React-component / helper shape. Matches
; var/let/const alike (both const and let use `lexical_declaration`, var uses
; the separate `variable_declaration` node type in this grammar).
[
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name.function
      value: [(arrow_function) (function_expression)]) @inner.function)
  (variable_declaration
    (variable_declarator
      name: (identifier) @name.function
      value: [(arrow_function) (function_expression)]) @inner.function)
] @definition.function

; exported arrow-function / function-expression variable —
; "export const Foo = () => {...}" / "export const Foo = function() {...}"
(export_statement
  declaration: (lexical_declaration
    (variable_declarator
      name: (identifier) @name.function
      value: [(arrow_function) (function_expression)]) @inner.function)) @definition.function

; method (decorators on methods are siblings in this grammar, not wrappers,
; so they never break this match — no separate decorated-method pattern needed)
(class_declaration
  name: (_) @name.class
  body: (class_body
    (method_definition
      name: (property_identifier) @name.method) @definition.method)) @definition.class_context

; class field assigned an arrow function / function expression — the common
; "handler = () => {...}" class-property-as-method pattern (binds `this`
; lexically, frequently used instead of a plain method for event handlers)
(class_declaration
  name: (_) @name.class
  body: (class_body
    (field_definition
      property: (property_identifier) @name.method
      value: [(arrow_function) (function_expression)]) @definition.method)) @definition.class_context

; whole class, bare
(class_declaration
  name: (_) @name.class) @definition.class

; exported class
(export_statement
  declaration: (class_declaration
    name: (_) @name.class) @inner.class) @definition.class
