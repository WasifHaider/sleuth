import pytest

from sleuth.ingest.parse import UnsupportedFileType, parse_source


def test_parse_source_python_no_errors():
    source = b"def foo():\n    return 1\n"
    tree, spec = parse_source(source, ".py")

    assert spec.key == "python"
    assert tree.root_node.type == "module"
    assert tree.root_node.has_error is False


def test_parse_source_javascript_no_errors():
    source = b"function foo() { return 1; }\n"
    tree, spec = parse_source(source, ".js")

    assert spec.key == "javascript"
    assert tree.root_node.has_error is False


def test_parse_source_unsupported_extension_raises():
    with pytest.raises(UnsupportedFileType):
        parse_source(b"irrelevant", ".rb")
