; bare module-level (or nested-in-function) function
(function_definition
  name: (identifier) @name.function) @definition.function

; decorated function — @definition.function is the *wrapper*, so decorator
; text is included; @inner.function is the raw node, used only for dedup
; against the bare pattern above which also matches it independently
(decorated_definition
  definition: (function_definition
    name: (identifier) @name.function) @inner.function) @definition.function

; plain method
(class_definition
  name: (identifier) @name.class
  body: (block
    (function_definition
      name: (identifier) @name.method) @definition.method)) @definition.class_context

; decorated method
(class_definition
  name: (identifier) @name.class
  body: (block
    (decorated_definition
      definition: (function_definition
        name: (identifier) @name.method) @inner.method) @definition.method)) @definition.class_context

; whole class, bare
(class_definition
  name: (identifier) @name.class) @definition.class

; whole class, decorated (e.g. @dataclass)
(decorated_definition
  definition: (class_definition
    name: (identifier) @name.class) @inner.class) @definition.class
