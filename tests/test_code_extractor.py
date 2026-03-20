"""Tests for AST-based code extraction with directed source/target."""

from __future__ import annotations

from hypergraph_code_explorer.extraction.code_extractor import CodeHyperedgeExtractor
from hypergraph_code_explorer.ingestion.chunker import Chunk


_SAMPLE_PYTHON = '''
import os
from pathlib import Path

class Session:
    """HTTP session."""

    def send(self, request, **kwargs):
        adapter = self.get_adapter(url=request.url)
        resp = adapter.send(request, **kwargs)
        return resp

    def get_adapter(self, url):
        return HTTPAdapter()

def helper(x: int) -> str:
    raise ValueError("bad")
'''


def test_extract_python_produces_directed_edges():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    assert len(edges) > 0

    # Check that all edges have sources and targets
    for edge in edges:
        assert len(edge.sources) > 0 or len(edge.targets) > 0
        assert edge.all_nodes == set(edge.sources) | set(edge.targets)


def test_extract_calls_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls_edges) > 0

    # Session.send should call self.get_adapter and adapter.send
    send_calls = [e for e in calls_edges if any("send" in s for s in e.sources)]
    assert len(send_calls) > 0


def test_extract_imports_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert len(imports) >= 2  # import os, from pathlib import Path

    # Source should be the module
    for imp in imports:
        assert "sessions" in imp.sources


def test_extract_defines_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    defines = [e for e in edges if e.edge_type == "DEFINES"]
    assert len(defines) > 0

    # Module should define Session and helper
    module_defines = [e for e in defines if "sessions" in e.sources]
    assert len(module_defines) > 0


def test_extract_inherits_edge():
    code = "class MyError(ValueError, RuntimeError):\n    pass"
    chunk = Chunk(
        text=code, chunk_id="test", source_path="errors.py",
        file_type="py", is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    inherits = [e for e in edges if e.edge_type == "INHERITS"]
    assert len(inherits) == 1
    assert "errors.MyError" in inherits[0].sources
    assert "ValueError" in inherits[0].targets
    assert "RuntimeError" in inherits[0].targets


def test_extract_raises_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    raises = [e for e in edges if e.edge_type == "RAISES"]
    assert len(raises) >= 1
    assert any("ValueError" in e.targets for e in raises)


def test_extract_signature_edge():
    chunk = Chunk(
        text=_SAMPLE_PYTHON,
        chunk_id="test_chunk",
        source_path="sessions.py",
        file_type="py",
        is_code=True,
    )
    extractor = CodeHyperedgeExtractor()
    edges = extractor.extract(chunk)

    sigs = [e for e in edges if e.edge_type == "SIGNATURE"]
    assert len(sigs) >= 1
    # helper(x: int) -> str
    helper_sig = [e for e in sigs if any("helper" in s for s in e.sources)]
    assert len(helper_sig) == 1
    assert "int" in helper_sig[0].targets
    assert "str" in helper_sig[0].targets
