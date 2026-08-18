from sleuth.ingest.chunk import chunk_source

PYTHON_SOURCE = b'''import os

MAX = 10

def foo():
    return 1

class Bar:
    def method_a(self):
        return 2

    def method_b(self):
        return 3

class Empty:
    pass
'''


def test_chunk_source_python_produces_expected_chunks():
    chunks = chunk_source(PYTHON_SOURCE, "pkg/mod.py", ".py")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert set(by_symbol) == {None, "foo", "Bar.method_a", "Bar.method_b", "Empty"}
    assert by_symbol[None].kind == "module"
    assert "import os" in by_symbol[None].code_text
    assert "MAX = 10" in by_symbol[None].code_text

    assert by_symbol["foo"].kind == "function"
    assert by_symbol["foo"].start_line == 5
    assert by_symbol["foo"].end_line == 6

    assert by_symbol["Bar.method_a"].kind == "method"
    assert by_symbol["Bar.method_b"].kind == "method"
    assert by_symbol["Empty"].kind == "class"

    # class with methods is not also emitted as its own whole-class chunk
    assert "Bar" not in by_symbol


JS_SOURCE = b'''const path = require("path");

function foo() {
  return 1;
}

class Bar {
  methodA() {
    return 2;
  }
}
'''


def test_chunk_source_javascript_produces_expected_chunks():
    chunks = chunk_source(JS_SOURCE, "src/mod.js", ".js")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert set(by_symbol) == {None, "foo", "Bar.methodA"}
    assert by_symbol["foo"].kind == "function"
    assert by_symbol["Bar.methodA"].kind == "method"


def test_chunk_source_unsupported_extension_falls_back_to_blank_line_blocks():
    chunks = chunk_source(b"puts 1\nputs 2\n", "script.rb", ".rb")

    assert len(chunks) == 1
    assert chunks[0].kind == "module"
    assert chunks[0].symbol_name is None
    assert chunks[0].code_text == "puts 1\nputs 2"


def test_chunk_source_unsupported_extension_splits_on_blank_lines():
    source = b"block one line\n\nblock two line\n"
    chunks = chunk_source(source, "script.rb", ".rb")

    assert len(chunks) == 2
    assert chunks[0].code_text == "block one line"
    assert chunks[1].code_text == "block two line"


PYTHON_DECORATED_SOURCE = b'''import os

@app.get("/x")
def handler():
    return 1


class Service:
    @staticmethod
    def helper():
        return 2

    def plain(self):
        return 3
'''


def test_chunk_source_python_decorated_module_function_is_its_own_chunk():
    chunks = chunk_source(PYTHON_DECORATED_SOURCE, "pkg/mod.py", ".py")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["handler"].kind == "function"
    assert "@app.get" in by_symbol["handler"].code_text
    assert "def handler" in by_symbol["handler"].code_text

    # decorator must not leak into the leftover module chunk, and handler
    # must not get merged into it either
    assert "@app.get" not in by_symbol[None].code_text
    assert "def handler" not in by_symbol[None].code_text


def test_chunk_source_python_decorated_method_is_tagged_as_method():
    chunks = chunk_source(PYTHON_DECORATED_SOURCE, "pkg/mod.py", ".py")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["Service.helper"].kind == "method"
    assert "@staticmethod" in by_symbol["Service.helper"].code_text
    assert by_symbol["Service.plain"].kind == "method"

    # class has methods, so no separate whole-class chunk
    assert "Service" not in by_symbol


TS_EXPORT_SOURCE = b'''export function plainHandler() {
  return 1;
}

@Injectable()
export class UserService {
  @Get()
  handler() {
    return 2;
  }

  plainMethod() {
    return 3;
  }
}
'''


def test_chunk_source_typescript_exported_function_is_its_own_chunk():
    chunks = chunk_source(TS_EXPORT_SOURCE, "src/service.ts", ".ts")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["plainHandler"].kind == "function"
    assert "export function plainHandler" in by_symbol["plainHandler"].code_text


def test_chunk_source_typescript_exported_class_methods_are_tagged():
    chunks = chunk_source(TS_EXPORT_SOURCE, "src/service.ts", ".ts")

    by_symbol = {c.symbol_name: c for c in chunks}

    assert by_symbol["UserService.handler"].kind == "method"
    assert by_symbol["UserService.plainMethod"].kind == "method"

    # exported class has methods, so no separate whole-class chunk
    assert "UserService" not in by_symbol


def test_chunk_source_caps_oversized_chunk_into_numbered_parts():
    body_lines = "\n".join(f"    x{i} = {i}" for i in range(400))
    source = f"def big():\n{body_lines}\n    return x0\n".encode()

    chunks = chunk_source(source, "pkg/big.py", ".py")

    parts = [c for c in chunks if c.symbol_name and c.symbol_name.startswith("big#")]
    assert len(parts) >= 2
    for part in parts:
        assert part.kind == "function"
        assert len(part.code_text) <= 4000
