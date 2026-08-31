import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

# Directory names that mean "this file is documentation/write-up, not the
# thing that actually runs" — regardless of its extension. The concrete bug
# this exists for: docs/recruiter-authentication.html, docs/progress.html,
# and similar hand-written architecture/status pages are real .html files
# that tree-sitter's HTML grammar parses just fine, so they were chunked and
# embedded exactly like a real .html template — and because they're
# expository PROSE about the architecture, they often score a HIGHER cosine
# match against an architecture-flavored question ("what's interesting about
# the auth flow") than the actual source file, silently crowding the real
# implementation out of the top-k results search_chunks returns. This marks
# any chunk under one of these directories (at any depth) so retrieval can
# deliberately prefer real code over documentation instead of treating both
# as equally "the codebase" — see search_chunks's prefer_code param.
DOC_DIR_NAMES = {"docs", "doc", "documentation"}


def is_doc_path(file_path: str) -> bool:
    parts = {p.lower() for p in PurePosixPath(file_path.replace("\\", "/")).parts}
    return bool(parts & DOC_DIR_NAMES)


@dataclass
class Chunk:
    file_path: str
    symbol_name: str | None
    kind: str  # 'function' | 'method' | 'class' | 'module'
    start_line: int
    end_line: int
    code_text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.code_text.encode("utf-8")).hexdigest()

    @property
    def is_doc(self) -> bool:
        return is_doc_path(self.file_path)


def format_chunk_context(chunk: Chunk, language: str) -> str:
    symbol = chunk.symbol_name or "(module level)"
    header = (
        f"# File: {chunk.file_path}\n"
        f"# {chunk.kind}: {symbol}\n"
        f"# Language: {language}\n\n"
    )
    return header + chunk.code_text
