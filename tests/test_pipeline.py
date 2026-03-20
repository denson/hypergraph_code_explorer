"""Tests for the pipeline (end-to-end on a small fixture)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypergraph_code_explorer.ingestion.converter import DocumentConverter
from hypergraph_code_explorer.ingestion.chunker import ContentAwareChunker, Chunk
from hypergraph_code_explorer.extraction.code_extractor import CodeHyperedgeExtractor
from hypergraph_code_explorer.graph.builder import HypergraphBuilder


_FIXTURE_CODE = '''
import os
from pathlib import Path

class FileReader:
    """Reads files from disk."""

    def read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def read_lines(self, path: str) -> list:
        content = self.read(path)
        return content.split("\\n")

def process(reader: FileReader, path: str) -> str:
    data = reader.read(path)
    return data.upper()
'''


def test_end_to_end_fixture():
    """Index a small fixture and verify graph structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write fixture
        fixture_path = Path(tmpdir) / "reader.py"
        fixture_path.write_text(_FIXTURE_CODE)

        # Convert
        converter = DocumentConverter()
        docs = converter.convert_directory(tmpdir)
        assert len(docs) == 1
        assert docs[0].is_code

        # Chunk
        chunker = ContentAwareChunker()
        chunks = chunker.chunk_all(docs)
        assert len(chunks) > 0

        # Extract
        extractor = CodeHyperedgeExtractor()
        edges = extractor.extract_all(chunks)
        assert len(edges) > 0

        # Build
        builder = HypergraphBuilder()
        added = builder.add_edges(edges)
        assert added > 0

        # Verify inverted index
        all_nodes = builder.get_all_nodes()
        assert "FileReader" in all_nodes or "reader.FileReader" in all_nodes

        # Verify edge types exist
        edge_types = {e.edge_type for e in builder._edge_store.values()}
        assert "DEFINES" in edge_types
        assert "CALLS" in edge_types or "IMPORTS" in edge_types


def test_converter_skips_hidden_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a hidden dir with a file
        hidden = Path(tmpdir) / ".git"
        hidden.mkdir()
        (hidden / "config.py").write_text("x = 1")

        # Create a normal file
        (Path(tmpdir) / "main.py").write_text("y = 2")

        converter = DocumentConverter()
        docs = converter.convert_directory(tmpdir)
        paths = [d.source_path for d in docs]

        assert any("main.py" in p for p in paths)
        assert not any(".git" in p for p in paths)


def test_chunker_python():
    chunks = []
    from hypergraph_code_explorer.ingestion.chunker import chunk_python_source
    chunks = chunk_python_source(_FIXTURE_CODE, "reader.py")
    assert len(chunks) > 0

    # Should have module preamble, class, methods, and standalone function
    symbol_names = [c.symbol_name for c in chunks if c.symbol_name]
    assert any("FileReader" in name for name in symbol_names)
