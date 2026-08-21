from dataclasses import dataclass

import tree_sitter_css as tscss
import tree_sitter_html as tshtml
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser, Tree

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())
CSS_LANGUAGE = Language(tscss.language())
HTML_LANGUAGE = Language(tshtml.language())

# tree-sitter-vue (vendored, see vendor/tree-sitter-vue/NOTICE.md) ships as
# SOURCE that gets compiled into a native extension at install time — unlike
# the pre-built wheels for the five grammars above, this needs a working C
# compiler on whatever machine runs `pip install`. Confirmed this succeeds in
# WSL (has gcc) but fails on a plain Windows venv with no MSVC Build Tools
# installed. Rather than make the whole sleuth.ingest.parse module (and
# everything that imports it — the entire ingest pipeline) unimportable on a
# machine without Vue support compiled, the import is optional: .vue support
# is simply absent (falls through to the line-based fallback chunker, same
# honest-degradation choice already made for .scss) if the compiled extension
# isn't present, rather than crashing at import time.
try:
    import tree_sitter_vue as tsvue

    VUE_LANGUAGE: Language | None = Language(tsvue.language())
except ImportError:
    VUE_LANGUAGE = None


@dataclass(frozen=True)
class LanguageSpec:
    key: str
    ts_language: Language


LANGUAGES: dict[str, LanguageSpec] = {
    ".py": LanguageSpec("python", PY_LANGUAGE),
    ".js": LanguageSpec("javascript", JS_LANGUAGE),
    ".ts": LanguageSpec("typescript", TS_LANGUAGE),
    # .jsx uses the SAME plain JS grammar as .js — tree-sitter-javascript already
    # parses JSX syntax embedded in a .js-shaped file with no errors, confirmed
    # by direct parse test, so no separate grammar/query file is needed here.
    ".jsx": LanguageSpec("javascript", JS_LANGUAGE),
    # .tsx needs its OWN grammar variant (language_tsx(), not language_typescript())
    # — the plain TypeScript grammar chokes (has_error=True) on JSX syntax like
    # `<div>{x}</div>`, confirmed by direct parse test. The TSX grammar produces
    # the same node shapes for functions/classes/methods as plain TS though, so
    # it reuses the existing typescript.scm query file unchanged — same "key"
    # below is what makes _load_query resolve to typescript.scm for .tsx too.
    ".tsx": LanguageSpec("typescript", TSX_LANGUAGE),
    ".css": LanguageSpec("css", CSS_LANGUAGE),
    ".html": LanguageSpec("html", HTML_LANGUAGE),
}
if VUE_LANGUAGE is not None:
    LANGUAGES[".vue"] = LanguageSpec("vue", VUE_LANGUAGE)
# NOTE: .scss deliberately NOT mapped to CSS_LANGUAGE here — tree-sitter-css's grammar
# produces real parse errors on SCSS-only syntax ($variables, nested rule blocks,
# @mixin/@include). SCSS needs its own tree-sitter-scss grammar; add it as a separate
# LanguageSpec entry + its own query file when that's actually wanted, don't silently
# feed .scss files through the plain CSS parser (chunk_source's fallback line-splitter
# will pick them up instead, which is honest even if less precise).


class UnsupportedFileType(Exception):
    pass


def parse_source(source_bytes: bytes, extension: str) -> tuple[Tree, LanguageSpec]:
    spec = LANGUAGES.get(extension)
    if spec is None:
        raise UnsupportedFileType(extension)

    parser = Parser(spec.ts_language)
    tree = parser.parse(source_bytes)
    return tree, spec
