"""
Code Hyperedge Extractor
========================
Uses tree-sitter to extract precise, structured hyperedges from code.
Zero LLM cost. Produces directed edges with sources and targets.

All supported languages (Python, JavaScript, TypeScript, Go, Rust, Java,
C, C++, Ruby, PHP) go through tree-sitter. A minimal regex fallback
handles truly unsupported file types.

IMPORTANT: Code files are extracted per-file (not per-chunk) to ensure
correct DEFINES edges and proper class-qualified names. Edges are then
associated back to their originating chunks by line range.

Edge types: CALLS, IMPORTS, DEFINES, INHERITS, SIGNATURE, RAISES.
"""

from __future__ import annotations

import re
from collections import defaultdict
from hashlib import md5
from pathlib import Path

from ..ingestion.chunker import Chunk
from ..models import EdgeType, HyperedgeRecord
from .treesitter_extractor import extract_file, is_language_supported


# ---------------------------------------------------------------------------
# Public extractor class
# ---------------------------------------------------------------------------

class CodeHyperedgeExtractor:
    """
    Extracts hyperedges from code chunks using tree-sitter for all supported
    languages. Falls back to regex for unsupported file types.

    All languages are extracted per-file to ensure correct class qualification
    and complete DEFINES edges. Edges are then associated back to their
    originating chunks by line range.
    """

    def extract(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Extract from a single chunk."""
        if not chunk.is_code:
            return []
        return self._extract_file_chunks(chunk.source_path, [chunk])

    def extract_all(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """
        Extract from all code chunks. Groups chunks by file for full-file
        parsing to get correct DEFINES and qualification.
        """
        edges: list[HyperedgeRecord] = []

        # Group all code chunks by source file
        chunks_by_file: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            if chunk.is_code:
                chunks_by_file[chunk.source_path].append(chunk)

        # Extract each file
        for source_path, file_chunks in chunks_by_file.items():
            edges.extend(self._extract_file_chunks(source_path, file_chunks))

        return edges

    def _extract_file_chunks(
        self, source_path: str, chunks: list[Chunk],
    ) -> list[HyperedgeRecord]:
        """Extract from a single file's chunks using tree-sitter."""
        file_type = chunks[0].file_type

        if not is_language_supported(file_type):
            return self._extract_regex_fallback(chunks)

        # Get full file source: try disk first, fall back to largest chunk
        full_source = self._reconstruct_file_source(source_path, chunks)

        return extract_file(full_source, source_path, file_type, chunks)

    def _reconstruct_file_source(
        self, source_path: str, chunks: list[Chunk],
    ) -> str:
        """
        Get the full file source. Try reading from disk first;
        fall back to concatenating non-overlapping chunks.
        """
        try:
            return Path(source_path).read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, OSError):
            pass

        if not chunks:
            return ""

        # Fall back: concatenate non-overlapping chunks (module, class, function
        # level — skip method chunks since they're contained in class chunks).
        # Sort by start_line to maintain order.
        top_chunks = [
            c for c in chunks
            if c.symbol_type in ("module", "class", "function", None)
        ]
        if not top_chunks:
            top_chunks = chunks

        top_chunks.sort(key=lambda c: c.start_line or 0)
        return "\n\n".join(c.text for c in top_chunks)

    def _extract_regex_fallback(self, chunks: list[Chunk]) -> list[HyperedgeRecord]:
        """Minimal regex fallback for languages tree-sitter doesn't support."""
        edges: list[HyperedgeRecord] = []
        for chunk in chunks:
            edges.extend(self._extract_generic_code(chunk))
        return edges

    def _extract_generic_code(self, chunk: Chunk) -> list[HyperedgeRecord]:
        """Regex-based extraction for unsupported languages."""
        edges: list[HyperedgeRecord] = []
        text = chunk.text
        source_path = chunk.source_path
        module_name = Path(source_path).stem

        func_pattern = re.compile(
            r"(?:function|def|func|fn|void|int|string|bool|auto)\s+(\w+)\s*\(",
            re.MULTILINE,
        )
        defined = [m.group(1) for m in func_pattern.finditer(text)]

        class_pattern = re.compile(r"(?:class|interface|struct|enum)\s+(\w+)", re.MULTILINE)
        classes = [m.group(1) for m in class_pattern.finditer(text)]

        all_symbols = defined + classes
        if all_symbols:
            eid = md5(f"DEFINES_{module_name}_{chunk.chunk_id[:8]}_generic".encode()).hexdigest()[:16]
            edges.append(HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} defines: {', '.join(all_symbols)}",
                edge_type=EdgeType.DEFINES,
                sources=[module_name],
                targets=all_symbols,
                source_path=source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
            ))

        import_pattern = re.compile(
            r"(?:import|require|include|using|from)\s+([\w./\"']+)",
            re.MULTILINE,
        )
        imports = [m.group(1).strip("\"'") for m in import_pattern.finditer(text)]
        if imports:
            eid = md5(f"IMPORTS_{module_name}_{chunk.chunk_id[:8]}_generic".encode()).hexdigest()[:16]
            edges.append(HyperedgeRecord(
                edge_id=eid,
                relation=f"module {module_name} imports: {', '.join(imports)}",
                edge_type=EdgeType.IMPORTS,
                sources=[module_name],
                targets=imports,
                source_path=source_path,
                chunk_id=chunk.chunk_id,
                chunk_text=chunk.text,
            ))

        return edges
