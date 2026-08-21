from functools import lru_cache
from pathlib import Path

from tree_sitter import Query

from sleuth.chunking import Chunk
from sleuth.ingest.css_chunker import chunk_css
from sleuth.ingest.html_chunker import chunk_html
from sleuth.ingest.parse import UnsupportedFileType, parse_source
from sleuth.ingest.query_chunker import run_query
from sleuth.ingest.vue_chunker import chunk_vue

QUERIES_DIR = Path(__file__).parent / "queries"
MAX_CHUNK_CHARS = 4000
# languages with no useful function/class/method shape — chunked structurally
# instead of via a tree-sitter query (see css_chunker.py)
STRUCTURAL_LANGUAGES = {"css"}


@lru_cache(maxsize=None)
def _load_query(language_key: str, ts_language) -> Query:
    query_text = (QUERIES_DIR / f"{language_key}.scm").read_text()
    return Query(ts_language, query_text)


def chunk_source(source_bytes: bytes, file_path: str, extension: str) -> list[Chunk]:
    try:
        tree, spec = parse_source(source_bytes, extension)
    except UnsupportedFileType:
        chunks = _fallback_chunk(source_bytes, file_path)
    else:
        if spec.key == "vue":
            # Vue is neither query-based (no single function/class shape to
            # match — a .vue file is template+script+style, three different
            # sub-languages) nor purely structural like CSS's flat rule list —
            # it recursively re-invokes chunk_source per <script>/<style>
            # block, so it gets its own dedicated branch rather than being
            # forced into STRUCTURAL_LANGUAGES.
            chunks = chunk_vue(tree.root_node, source_bytes, file_path)
        elif spec.key == "html":
            # Same "recursively re-invokes chunk_source" shape as Vue, but
            # HTML's script/style tags are scattered anywhere in the tree
            # (not conveniently top-level like a Vue SFC), so it gets its own
            # branch and its own tree-walking logic (html_chunker.py).
            chunks = chunk_html(tree.root_node, source_bytes, file_path)
        elif spec.key in STRUCTURAL_LANGUAGES:
            chunks = chunk_css(tree.root_node, source_bytes, file_path)
        else:
            query = _load_query(spec.key, spec.ts_language)
            chunks = run_query(query, tree.root_node, source_bytes, file_path)

    return _cap_chunk_sizes(chunks)


def _fallback_chunk(source_bytes: bytes, file_path: str) -> list[Chunk]:
    text = source_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    blocks: list[tuple[int, int, list[str]]] = []
    current: list[str] = []
    start = 1
    for lineno, line in enumerate(lines, start=1):
        if line.strip() == "":
            if current:
                blocks.append((start, lineno - 1, current))
                current = []
            continue
        if not current:
            start = lineno
        current.append(line)
    if current:
        blocks.append((start, len(lines), current))

    return [
        Chunk(
            file_path=file_path,
            symbol_name=None,
            kind="module",
            start_line=block_start,
            end_line=block_end,
            code_text="\n".join(block_lines),
        )
        for block_start, block_end, block_lines in blocks
    ]


def _cap_chunk_sizes(chunks: list[Chunk]) -> list[Chunk]:
    capped: list[Chunk] = []
    for chunk in chunks:
        if len(chunk.code_text) <= MAX_CHUNK_CHARS:
            capped.append(chunk)
            continue

        lines = chunk.code_text.split("\n")
        part_lines: list[str] = []
        part_start = chunk.start_line
        part_index = 1
        line_no = chunk.start_line

        def flush() -> None:
            nonlocal part_lines, part_index, part_start
            if not part_lines:
                return
            symbol_name = f"{chunk.symbol_name}#{part_index}" if chunk.symbol_name else f"#{part_index}"
            capped.append(
                Chunk(
                    file_path=chunk.file_path,
                    symbol_name=symbol_name,
                    kind=chunk.kind,
                    start_line=part_start,
                    end_line=part_start + len(part_lines) - 1,
                    code_text="\n".join(part_lines),
                )
            )
            part_index += 1
            part_lines = []

        for line in lines:
            candidate = "\n".join(part_lines + [line])
            if part_lines and len(candidate) > MAX_CHUNK_CHARS:
                flush()
                part_start = line_no
            part_lines.append(line)
            line_no += 1
        flush()

    return capped
