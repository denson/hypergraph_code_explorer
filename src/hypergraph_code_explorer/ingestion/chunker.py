"""
Content-Aware Chunker
=====================
Splits documents into chunks that respect content structure.
  - Code files  → chunked by function/class boundary (AST-aware)
  - Markdown/docs → chunked by heading or semantic paragraph boundary
  - Generic text → RecursiveCharacterTextSplitter fallback
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path

from .converter import ConvertedDocument


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """A piece of text ready for hyperedge extraction."""
    text: str
    chunk_id: str
    source_path: str
    file_type: str
    is_code: bool
    symbol_name: str | None = None
    symbol_type: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    heading: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = md5(
                f"{self.source_path}:{self.text[:200]}".encode()
            ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Python AST-based code chunker
# ---------------------------------------------------------------------------

def _get_source_segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def chunk_python_source(source: str, source_path: str) -> list[Chunk]:
    """Parse Python source with ast and produce one Chunk per top-level symbol."""
    chunks: list[Chunk] = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return chunk_text(source, source_path, file_type="py", is_code=True)

    def make_chunk(text: str, name: str, kind: str, start: int, end: int) -> Chunk:
        cid = md5(f"{source_path}:{name}:{start}".encode()).hexdigest()[:16]
        return Chunk(
            text=text, chunk_id=cid, source_path=source_path,
            file_type="py", is_code=True,
            symbol_name=name, symbol_type=kind,
            start_line=start, end_line=end,
        )

    # Preamble: imports and module docstring
    preamble_lines = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seg = _get_source_segment(source, node)
            if seg:
                preamble_lines.append(seg)

    module_doc = ast.get_docstring(tree)
    if module_doc or preamble_lines:
        preamble_text = (f'"""{module_doc}"""\n\n' if module_doc else "")
        preamble_text += "\n".join(preamble_lines)
        if preamble_text.strip():
            cid = md5(f"{source_path}:__preamble__".encode()).hexdigest()[:16]
            chunks.append(Chunk(
                text=preamble_text, chunk_id=cid, source_path=source_path,
                file_type="py", is_code=True,
                symbol_name="__module__", symbol_type="module",
            ))

    # Top-level classes and functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = _get_source_segment(source, node)
            if text:
                chunks.append(make_chunk(
                    text, node.name, "function",
                    node.lineno, node.end_lineno or node.lineno,
                ))
        elif isinstance(node, ast.ClassDef):
            class_text = _get_source_segment(source, node)
            if class_text:
                chunks.append(make_chunk(
                    class_text, node.name, "class",
                    node.lineno, node.end_lineno or node.lineno,
                ))
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_text = _get_source_segment(source, child)
                    if method_text:
                        chunks.append(make_chunk(
                            method_text, f"{node.name}.{child.name}", "method",
                            child.lineno, child.end_lineno or child.lineno,
                        ))

    if not chunks:
        return chunk_text(source, source_path, file_type="py", is_code=True)

    return chunks


# ---------------------------------------------------------------------------
# Markdown / doc chunker
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_markdown(text: str, source_path: str, file_type: str = "md",
                   max_chunk_size: int = 2000) -> list[Chunk]:
    """Split markdown by headings."""
    sections: list[tuple[str, str]] = []
    last_end = 0
    current_heading = "__top__"

    for m in _HEADING_RE.finditer(text):
        body = text[last_end:m.start()].strip()
        if body:
            sections.append((current_heading, body))
        current_heading = m.group(2).strip()
        last_end = m.end()

    body = text[last_end:].strip()
    if body:
        sections.append((current_heading, body))

    chunks: list[Chunk] = []
    for heading, body in sections:
        if len(body) > max_chunk_size:
            paragraphs = re.split(r"\n\n+", body)
            buffer = ""
            for para in paragraphs:
                if len(buffer) + len(para) > max_chunk_size and buffer:
                    cid = md5(f"{source_path}:{heading}:{buffer[:50]}".encode()).hexdigest()[:16]
                    chunks.append(Chunk(
                        text=buffer.strip(), chunk_id=cid,
                        source_path=source_path, file_type=file_type,
                        is_code=False, heading=heading,
                    ))
                    buffer = para
                else:
                    buffer = (buffer + "\n\n" + para).strip()
            if buffer.strip():
                cid = md5(f"{source_path}:{heading}:{buffer[:50]}".encode()).hexdigest()[:16]
                chunks.append(Chunk(
                    text=buffer.strip(), chunk_id=cid,
                    source_path=source_path, file_type=file_type,
                    is_code=False, heading=heading,
                ))
        else:
            cid = md5(f"{source_path}:{heading}:{body[:50]}".encode()).hexdigest()[:16]
            chunks.append(Chunk(
                text=body, chunk_id=cid,
                source_path=source_path, file_type=file_type,
                is_code=False, heading=heading,
            ))

    if not chunks:
        return chunk_text(text, source_path, file_type=file_type, is_code=False)

    return chunks


# ---------------------------------------------------------------------------
# Generic text fallback chunker
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    source_path: str,
    file_type: str = "txt",
    is_code: bool = False,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> list[Chunk]:
    """Simple overlapping character-based chunker. Used as fallback."""
    chunks: list[Chunk] = []
    start = 0
    idx = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        fragment = text[start:end]
        cid = md5(f"{source_path}:{idx}:{fragment[:50]}".encode()).hexdigest()[:16]
        chunks.append(Chunk(
            text=fragment, chunk_id=cid,
            source_path=source_path, file_type=file_type,
            is_code=is_code, metadata={"chunk_index": idx},
        ))
        start += chunk_size - chunk_overlap
        idx += 1

    return chunks


# ---------------------------------------------------------------------------
# Main chunker dispatcher
# ---------------------------------------------------------------------------

class ContentAwareChunker:
    """Dispatches to the right chunking strategy based on file type."""

    def __init__(self, max_chunk_size: int = 2000, chunk_overlap: int = 200):
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, doc: ConvertedDocument) -> list[Chunk]:
        if doc.is_code and doc.file_type == "py":
            return chunk_python_source(doc.markdown, doc.source_path)
        elif doc.file_type in ("md", "rst", "txt") or not doc.is_code:
            return chunk_markdown(
                doc.markdown, doc.source_path,
                file_type=doc.file_type,
                max_chunk_size=self.max_chunk_size,
            )
        else:
            return chunk_text(
                doc.markdown, doc.source_path,
                file_type=doc.file_type, is_code=True,
                chunk_size=self.max_chunk_size,
                chunk_overlap=self.chunk_overlap,
            )

    def chunk_all(self, docs: list[ConvertedDocument]) -> list[Chunk]:
        all_chunks: list[Chunk] = []
        for doc in docs:
            all_chunks.extend(self.chunk(doc))
        return all_chunks
