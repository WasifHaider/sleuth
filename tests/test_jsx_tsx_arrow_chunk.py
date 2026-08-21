from sleuth.ingest.chunk import chunk_source
from sleuth.ingest.parse import UnsupportedFileType, parse_source

# --- arrow-function-component gap: this is the whole reason this file exists.
# The pre-existing JS/TS query files only matched `function_declaration` /
# `class_declaration` — they silently missed the extremely common React
# pattern `const Foo = () => {...}`, `export const Foo = () => {...}`, and
# `export default function` (dedup-only, but worth locking in), plus
# class-field arrow handlers. These tests pin that gap closed.

JS_ARROW_SOURCE = b'''import React from "react";

const Foo = () => {
  return 1;
};

export const Bar = (props) => {
  return props.x;
};

var Legacy = function() {
  return 99;
};

export default function Main() {
  return null;
}

class Widget {
  handler = () => {
    return 1;
  };

  plainMethod() {
    return 2;
  }
}
'''


def test_chunk_source_js_bare_arrow_const_is_chunked_as_function():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Foo"].kind == "function"
    assert "const Foo" in by_symbol["Foo"].code_text
    assert "=>" in by_symbol["Foo"].code_text


def test_chunk_source_js_exported_arrow_const_is_chunked_as_function():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Bar"].kind == "function"
    assert "export const Bar" in by_symbol["Bar"].code_text


def test_chunk_source_js_var_function_expression_is_chunked():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Legacy"].kind == "function"
    assert "var Legacy" in by_symbol["Legacy"].code_text


def test_chunk_source_js_export_default_function_still_chunked():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Main"].kind == "function"
    assert "export default function Main" in by_symbol["Main"].code_text


def test_chunk_source_js_class_field_arrow_handler_is_tagged_as_method():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Widget.handler"].kind == "method"
    assert "=>" in by_symbol["Widget.handler"].code_text
    assert by_symbol["Widget.plainMethod"].kind == "method"
    # class has methods (including the arrow one) -> no separate whole-class chunk
    assert "Widget" not in by_symbol


def test_chunk_source_js_arrow_functions_not_duplicated_in_leftover_module_chunk():
    chunks = chunk_source(JS_ARROW_SOURCE, "src/app.js", ".js")
    by_symbol = {c.symbol_name: c for c in chunks}

    module_chunk = by_symbol[None]
    assert "const Foo" not in module_chunk.code_text
    assert "export const Bar" not in module_chunk.code_text
    assert "var Legacy" not in module_chunk.code_text


TS_ARROW_SOURCE = b'''import { Component } from "@angular/core";

@Component({selector: "app-root"})
export class AppComponent {
  ngOnInit() {}

  handler = () => {
    console.log(1);
  };
}

export const useCounter = () => {
  return 1;
};

const helper = (): number => 42;
'''


def test_chunk_source_ts_arrow_const_with_return_type_is_chunked():
    chunks = chunk_source(TS_ARROW_SOURCE, "src/util.ts", ".ts")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["helper"].kind == "function"
    assert "const helper" in by_symbol["helper"].code_text


def test_chunk_source_ts_exported_arrow_const_is_chunked():
    chunks = chunk_source(TS_ARROW_SOURCE, "src/util.ts", ".ts")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["useCounter"].kind == "function"


def test_chunk_source_ts_decorated_class_field_arrow_handler_is_tagged_method():
    chunks = chunk_source(TS_ARROW_SOURCE, "src/util.ts", ".ts")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["AppComponent.ngOnInit"].kind == "method"
    assert by_symbol["AppComponent.handler"].kind == "method"
    assert "=>" in by_symbol["AppComponent.handler"].code_text


# --- .jsx / .tsx extension + grammar registration

def test_parse_source_jsx_uses_javascript_grammar_no_errors():
    source = b'const Foo = () => <div>hi</div>;\n'
    tree, spec = parse_source(source, ".jsx")

    assert spec.key == "javascript"
    assert tree.root_node.has_error is False


def test_parse_source_tsx_uses_tsx_grammar_no_errors():
    source = b'const Foo: React.FC = () => <div>hi</div>;\n'
    tree, spec = parse_source(source, ".tsx")

    assert spec.key == "typescript"
    assert tree.root_node.has_error is False


def test_parse_source_plain_typescript_grammar_errors_on_jsx_syntax():
    # sanity check for the doc claim that .tsx genuinely needs its own grammar
    # variant (language_tsx()) rather than reusing plain .ts's language_typescript()
    source = b'const Foo: React.FC = () => <div>hi</div>;\n'
    tree, spec = parse_source(source, ".ts")

    assert spec.key == "typescript"
    assert tree.root_node.has_error is True


def test_chunk_source_jsx_react_component_and_export_default_are_chunked():
    source = b'''import React from "react";

const Foo = (props) => {
  return <div>{props.x}</div>;
};

export default function App() {
  return <Foo x={1} />;
}
'''
    chunks = chunk_source(source, "src/App.jsx", ".jsx")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Foo"].kind == "function"
    assert by_symbol["App"].kind == "function"


def test_chunk_source_tsx_typed_component_and_class_component_are_chunked():
    source = b'''import React from "react";

const Foo: React.FC<{x: number}> = (props) => {
  return <div>{props.x}</div>;
};

class Widget extends React.Component {
  render() {
    return <div />;
  }
}
'''
    chunks = chunk_source(source, "src/App.tsx", ".tsx")
    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Foo"].kind == "function"
    assert by_symbol["Widget.render"].kind == "method"


def test_chunk_source_unsupported_extension_still_raises_for_ruby():
    try:
        parse_source(b"irrelevant", ".rb")
        assert False, "expected UnsupportedFileType"
    except UnsupportedFileType:
        pass
