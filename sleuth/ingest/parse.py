from dataclasses import dataclass

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser, Tree

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())


@dataclass(frozen=True)
class LanguageSpec:
    key: str
    ts_language: Language


LANGUAGES: dict[str, LanguageSpec] = {
    ".py": LanguageSpec("python", PY_LANGUAGE),
    ".js": LanguageSpec("javascript", JS_LANGUAGE),
    ".ts": LanguageSpec("typescript", TS_LANGUAGE),
}


class UnsupportedFileType(Exception):
    pass


def parse_source(source_bytes: bytes, extension: str) -> tuple[Tree, LanguageSpec]:
    spec = LANGUAGES.get(extension)
    if spec is None:
        raise UnsupportedFileType(extension)

    parser = Parser(spec.ts_language)
    tree = parser.parse(source_bytes)
    return tree, spec
