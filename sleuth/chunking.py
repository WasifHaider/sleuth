import hashlib
from dataclasses import dataclass


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


def format_chunk_context(chunk: Chunk, language: str) -> str:
    symbol = chunk.symbol_name or "(module level)"
    header = (
        f"# File: {chunk.file_path}\n"
        f"# {chunk.kind}: {symbol}\n"
        f"# Language: {language}\n\n"
    )
    return header + chunk.code_text
